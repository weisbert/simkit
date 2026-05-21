# 3-Zone Deployment Pipeline

simkit is developed on a home Linux machine with internet, transits a
yellow-zone Windows box that can fetch wheels, and finally lands on a
red-zone Linux box with no internet.

This directory contains the 4 scripts that move simkit through that
pipeline. **The home machine is the source of truth.** Yellow Windows
is pure transit (git pull → download wheels → tarball). Red Linux is
deployment (unpack → venv).

```
   home Linux              yellow Windows            red Linux
   (internet)              (internet, transit)       (offline)
   ─────────               ──────────────────        ──────────
   git push   ─────►   git pull                                  
                       download_wheels.py                        
                       make_payload.py    ────►  unpack_payload.sh
                                                  deploy_venv.sh  
```

---

## The shared-venv model (why a code deploy is fast)

The venv depends only on `requirements.lock.txt`; the code does not. So
the venv is **shared** across deploys and lives at a stable path:

```
<DEPLOYS>/
  venv/                      # the ONE venv — rebuilt only when the lock changes
    .simkit_lockhash         # sha256 of the lock it was built from
    lib/.../simkit_src.pth   # one line: <DEPLOYS>/current/python
  current  ->  simkit_<sha>/ # symlink to the active code deploy
  simkit_<sha>/              # a code deploy — python/ skill/ scripts/ ...
```

`simkit` is **not** pip-installed. The venv imports it through the
static `simkit_src.pth`, which points at `<DEPLOYS>/current/python` —
so a code-only deploy is just **"extract new dir + flip the `current`
symlink"**. `deploy_venv.sh` hashes `requirements.lock.txt`: if it
matches the venv's stored hash, the venv is reused untouched (no pip);
if it differs, the venv is rebuilt. Deploy times:

| Deploy | What runs |
|---|---|
| Code / SKILL / docs only | flip symlink, smoke test — seconds |
| `requirements.lock.txt` changed | full venv rebuild from wheels |
| First-ever deploy on a host | full venv build (bootstraps `<DEPLOYS>/venv`) |

---

## Yellow Windows — fetch wheels + bundle

Prereq: git pull from home is up to date, Python 3.11 installed.

```bash
cd simkit
python scripts/download_wheels.py     # → vendor/wheels/*.whl
python scripts/make_payload.py        # → dist/simkit_<date>_<sha>.tar.gz
                                      # → dist/simkit_<date>_<sha>.manifest.txt
```

The `.tar.gz` + `.manifest.txt` is the payload. Both files must travel
together (manifest holds the SHA256 used for integrity check on red).

**Customizing the download:**

```bash
python scripts/download_wheels.py --clean                # wipe vendor/wheels first
python scripts/download_wheels.py --python-version 3.12  # different target Python
```

Incremental by default: re-runs use pip's HTTP cache and are fast.

**Customizing the bundle:**

```bash
python scripts/make_payload.py --name simkit_phase4_bringup   # custom filename
python scripts/make_payload.py --verbose                      # log every excluded member
```

Excluded by default: `.git/` `.venv/` `__pycache__/` `*.pyc` IDE files
DuckDB databases skillbridge logs `*.swp` etc.

### Code-only iteration — skip the wheel bundle

When you only changed code (no `requirements*.txt` diff), skip the 70 MB
wheel bundle. The tarball drops from ~80 MB to a few MB; red zone reuses
wheels from the previous deploy automatically.

```bash
python scripts/make_payload.py --no-wheels
# → dist/simkit_<date>_<sha>_code.tar.gz   (the '_code' suffix marks it)
```

On red, the same `unpack_payload.sh` handles both modes:

```bash
bash <DEPLOYS>/current/scripts/unpack_payload.sh /path/to/simkit_<...>_code.tar.gz
# auto-copies wheels from <DEPLOYS>/current/vendor/wheels/ into the new deploy.
cd <DEPLOYS>/simkit_<...>_code
bash scripts/deploy_venv.sh
```

Hard requirement: a prior **full-payload** deploy must already exist at
`<DEPLOYS>/current/`. If not, `unpack_payload.sh` exits with code 5 and
tells you to do a full deploy first to seed wheels.

When to use which:

| Diff type | Use |
|---|---|
| Code / SKILL / docs only | `--no-wheels` (fast, light)|
| `requirements.txt` or `requirements.lock.txt` changed | Full bundle (default) |
| First-ever deploy to a host | Full bundle (no `current/` to reuse from) |

### CRLF guard (yellow Windows + bash `.sh` files)

Yellow Windows' default `core.autocrlf=true` rewrites `.sh` files as
CRLF on checkout. `set -euo pipefail\r` then chokes red-zone bash.
Three layers of defense are now in place; you don't need to do anything
extra:

1. `.gitattributes` — git won't introduce CRLF on checkout (preventive)
2. `make_payload.py` — normalizes CRLF→LF for `.sh`/`.py`/`.il`/`.ils`
   members AT PACK TIME; prints `[normalize] N text file(s) had CRLF`
   if your working tree was dirty (informational — the tarball is clean)
3. `unpack_payload.sh` — `sed`-strips any residual CRLF from
   `scripts/*.sh` post-extract (last-resort)

If you've been pulling from yellow before `.gitattributes` landed, your
working tree may still have CRLF in `.sh` files. One-time renormalize:
```bash
git pull
git add --renormalize . && git checkout -- .
```

---

## Red Linux — unpack + venv

Prereq: tarball + manifest transferred (USB, internal share, whatever);
Python 3.11 on PATH.

**Pick where your deploys live.** The scripts don't assume `$HOME` —
common choices are `~/simkit_deploys/`, `<workarea>/simkit_deploys/`,
or `/opt/simkit/`. Call this `<DEPLOYS>` from here on.

### First-ever deploy

Before any deploy exists, the scripts are still inside the tarball —
chicken-and-egg. Use bare `tar` for this one time:

```bash
mkdir -p <DEPLOYS> && cd <DEPLOYS>
tar -xzf /path/to/simkit_20260519_214a6ec.tar.gz
cd simkit_20260519_214a6ec
bash scripts/deploy_venv.sh
```

Worked example with `workarea/simkit_deploys/`:

```bash
cd /home/me/workarea
mkdir -p simkit_deploys && cd simkit_deploys
tar -xzf ~/payload.tar.gz       # tarball wherever you parked it
cd simkit_20260519_214a6ec
bash scripts/deploy_venv.sh
```

`deploy_venv.sh` builds the shared `<DEPLOYS>/venv` (fully isolated —
**does NOT** inherit from the red-zone global Python), installs every
package from `vendor/wheels/`, writes the `simkit_src.pth` + the `pvt`
wrapper, runs the smoke tests, and **atomically points
`<DEPLOYS>/current` → this deploy**.

After it's done — note the venv path is stable, it does not change per
deploy:

```bash
source <DEPLOYS>/venv/bin/activate
pvt --help
```

### Subsequent iterations

The `current/scripts/` dir from the previous deploy is your bootstrap.
`unpack_payload.sh` auto-detects `<DEPLOYS>` from its own location
(2 levels up from itself), so you don't have to repeat the path.

```bash
bash <DEPLOYS>/current/scripts/unpack_payload.sh /path/to/new.tar.gz
cd <DEPLOYS>/$(basename /path/to/new.tar.gz .tar.gz)
bash scripts/deploy_venv.sh
```

Worked example (tarball already inside the deploys dir):

```bash
# tarball lives at <DEPLOYS>/new_payload.tar.gz
bash <DEPLOYS>/current/scripts/unpack_payload.sh <DEPLOYS>/new_payload.tar.gz
cd <DEPLOYS>/simkit_<newdate>_<newsha>
bash scripts/deploy_venv.sh
```

**Target dir resolution chain in `unpack_payload.sh`** (first match wins):

1. Explicit 2nd arg (`bash unpack_payload.sh tarball.tar.gz /custom/path`)
2. `$SIMKIT_DEPLOYS_DIR` env var
3. Auto-detect 2 levels up from script's own location
4. Current working directory (last resort)

If you put `export SIMKIT_DEPLOYS_DIR=/home/me/workarea/simkit_deploys`
in your `.bashrc`, every invocation lands in the right place without
remembering the absolute path.

### Useful flags

```bash
bash scripts/deploy_venv.sh --force       # rebuild the shared venv even if the lock is unchanged
bash scripts/deploy_venv.sh --no-current  # don't flip the 'current' symlink
bash scripts/deploy_venv.sh --no-smoke    # skip post-install smoke tests
```

### Daily activation (永远是这一行)

```bash
source <DEPLOYS>/venv/bin/activate
pvt run review.json
```

The venv path is fixed — it no longer changes per deploy, so this is
the same line forever. (csh: `source <DEPLOYS>/venv/bin/activate.csh`.)

### Rollback to an earlier deploy

Instant — just retarget the symlink, no rebuild:

```bash
ln -sfn <DEPLOYS>/simkit_<earlier-date>_<sha> <DEPLOYS>/current
# the venv's simkit_src.pth follows `current`, so the next `pvt` run
# uses the earlier code. No reinstall.
```

Caveat: rollback only flips *code*. If the earlier deploy needed a
different `requirements.lock.txt`, rebuild the venv from that deploy:
`cd <DEPLOYS>/current && bash scripts/deploy_venv.sh --force`.

### Cleanup old deploys

```bash
bash <DEPLOYS>/current/scripts/cleanup_deploys.sh --keep 3 --dry-run
bash <DEPLOYS>/current/scripts/cleanup_deploys.sh --keep 3
```

`cleanup_deploys.sh` auto-detects `<DEPLOYS>` the same way
`unpack_payload.sh` does. Override with `--deploys-dir`.

Cleanup rules:
- Keeps the N most-recently-modified deploys (default `--keep 3`).
- **Always protects the deploy that `current` points to**, even if it
  would otherwise fall outside the keep cutoff (rollback semantics).
  This is also what keeps the shared venv's `simkit_src.pth` valid —
  the code dir it resolves to is never pruned.
- The shared `venv/` is **NOT** touched — cleanup only deletes
  `simkit_*/` directories, and `venv/` is not one. (Deleting an old
  `simkit_*/` also reclaims any legacy per-deploy `.venv/` it carried
  from before the shared-venv model.)
- `*.tar.gz` and `*.manifest.txt` files are **NOT** touched. Clean
  tarballs by hand:
  ```bash
  rm <DEPLOYS>/*.tar.gz <DEPLOYS>/*.manifest.txt
  ```
- `--dry-run` lists what *would* be deleted with sizes + total freed.
  Always preview before deleting in shared environments.

---

## What's locked

`requirements.lock.txt` lives at repo root and is the single source of
truth for what gets installed across all 3 zones. It's a full freeze
of every dependency including transitive ones.

When you want to bump a version:

1. Edit `requirements.txt` (top-level intent)
2. Re-freeze on home:
   ```bash
   python3 -m venv /tmp/relock
   source /tmp/relock/bin/activate
   pip install --upgrade pip setuptools wheel    # ensure latest build backend
   pip install -r requirements-dev.txt
   # --all keeps setuptools + wheel in the freeze (build backend deps);
   # we exclude pip itself because venv bootstraps it via ensurepip.
   pip freeze --all | grep -v "^pip==" > requirements.lock.txt
   deactivate && rm -rf /tmp/relock
   ```
3. Commit + push.
4. Yellow Windows: `git pull` then re-run `download_wheels.py --clean`.
5. Rebuild + redeploy tarball as usual.

---

## Troubleshooting

**`pip download` on Windows fetches the wrong wheel.** The script forces
`--platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --abi cp311
--implementation cp --only-binary=:all:`. Both platform tags name the SAME
baseline (glibc 2.17); newer baselines (e.g. `manylinux_2_28`) are
deliberately NOT in `DEFAULT_PLATFORMS` because red zone is RHEL7-era
glibc 2.17 — accepting newer wheels would silently break the deploy.

**`pvt gui` says "PyQt5 is not installed" but `pip list` shows it.**
On EDA hosts, `LD_LIBRARY_PATH` often includes Cadence's own Qt5
(e.g. `/software/public/qt/5.15.3_xcb/lib`), which shadows the wheel's
bundled Qt 5.15.18 at import time. `deploy_venv.sh` automatically
prepends the wheel's Qt5 lib dir to `LD_LIBRARY_PATH` inside the
generated `.venv/bin/activate` and `activate.csh` — re-source the
activate script. If the issue persists, run
`python -c "from PyQt5.QtWidgets import QApplication"` to see the real
error; since Phase 4, `pvt gui` itself distinguishes "missing" (exit 4)
from "fails to load" (exit 5) and prints the underlying error.

**`pip install` reports `Could not find a version that satisfies
duckdb==X` on red zone.** Wheel platform mismatch. duckdb dropped
manylinux_2_17 wheels at v1.3 — the lock pins duckdb to the last
glibc-2.17-compatible version. If a future bump needs a newer duckdb,
either find another version that still ships manylinux2014 wheels, or
build duckdb from source on a glibc-2.17 host.

**`deploy_venv.sh` reports SHA256 mismatch.** The tarball was corrupted
in transit. Re-transfer.

**`deploy_venv.sh` reports `pvt` not on PATH after install.** Re-source
the venv: `deactivate; source .venv/bin/activate`. If still missing,
check that `pyproject.toml` is in the unpacked dir and that
`pip install -e .` succeeded (re-run with `--force`).

**Editable install fails with `setuptools>=61` not found.** This is
PEP 517 build isolation trying to fetch setuptools online. `deploy_venv.sh`
passes `--no-build-isolation` to use the venv's bundled setuptools
instead. If you see this error, you're probably running pip install
manually without that flag — re-run via `deploy_venv.sh` or add
`--no-build-isolation` yourself.

**Editable install fails with `invalid command 'bdist_wheel'`.** The
`wheel` package isn't installed in the venv. The lock file pins
`wheel==X.Y` explicitly so this shouldn't happen on a fresh deploy,
but if your venv predates that pin, run `pip install --no-index
--find-links=vendor/wheels wheel` then retry.

**Red-zone Python is different micro version (e.g. 3.11.4 vs 3.11.13).**
Same-minor is binary compatible; cp311 wheels work on both. Across
minor versions (3.11 → 3.12) you must regenerate wheels with
`--python-version 3.12`.
