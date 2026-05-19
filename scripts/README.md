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

`deploy_venv.sh` creates `.venv/` (fully isolated — **does NOT**
inherit from the red-zone global Python), installs every package from
`vendor/wheels/`, installs simkit editable, runs 4 smoke tests, and
**atomically points `<DEPLOYS>/current` → this deploy** so the active
deploy has a stable, version-independent path.

After it's done:

```bash
cd <DEPLOYS>/current
source .venv/bin/activate
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
bash scripts/deploy_venv.sh --force       # wipe + recreate existing .venv
bash scripts/deploy_venv.sh --no-current  # don't touch the 'current' symlink
bash scripts/deploy_venv.sh --no-smoke    # skip post-install smoke tests
```

### Daily activation (永远是这一行)

```bash
cd <DEPLOYS>/current
source .venv/bin/activate
pvt run review.json
```

### Rollback to an earlier deploy

Instant — just retarget the symlink, no rebuild:

```bash
ln -sfn <DEPLOYS>/simkit_<earlier-date>_<sha> <DEPLOYS>/current
# next 'cd current && source .venv/bin/activate' uses the earlier env
```

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
- `*.tar.gz` and `*.manifest.txt` files are **NOT** touched — only
  `simkit_*/` directories. Clean tarballs by hand:
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
`--platform manylinux2014_x86_64 --abi cp311 --implementation cp
--only-binary=:all:`. If a package has no manylinux wheel (rare), pip
falls back through `manylinux_2_28_x86_64` and `manylinux_2_17_x86_64`
in order.

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
