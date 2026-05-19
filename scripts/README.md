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

```bash
bash scripts/unpack_payload.sh simkit_20260519_214a6ec.tar.gz
# → extracted to ~/simkit_deploys/simkit_20260519_214a6ec/
```

Default target is `~/simkit_deploys/`; override with second arg:

```bash
bash scripts/unpack_payload.sh simkit_20260519_214a6ec.tar.gz /opt/simkit
```

Then create the venv from the unpacked dir:

```bash
cd ~/simkit_deploys/simkit_20260519_214a6ec
bash scripts/deploy_venv.sh
```

This creates `.venv/` (fully isolated — **does NOT** inherit from the
red-zone global Python), installs every package from `vendor/wheels/`,
installs simkit in editable mode, and runs 4 smoke tests.

After it's done:

```bash
source .venv/bin/activate
pvt --help
```

**Re-deploying / upgrading:**

```bash
bash scripts/deploy_venv.sh --force    # wipe + recreate .venv
```

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
