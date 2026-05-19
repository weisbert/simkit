# Phase 4 §2 — PyQt5 deps deployment checklist

What this lands on red zone: `PyQt5==5.15.9`, `PyQt5-Qt5==5.15.18`,
`PyQt5-sip==12.18.0`, `QtAwesome==1.4.2`, `QtPy==2.4.3`,
`pytest-qt==4.5.0`, `typing_extensions==4.15.0` — 7 new wheels,
~70 MB extra payload (PyQt5-Qt5 alone is 61 MB).

Lock file + `requirements.txt` + `requirements-dev.txt` already updated
on home (this commit). Yellow + red zone steps below — copy-paste in
order.

---

## Step 1 — home Linux (already done)

Done in this commit. No action required. For reference:
```bash
git pull
# requirements.txt + requirements-dev.txt + requirements.lock.txt
# already include PyQt5 / QtAwesome / pytest-qt.
```

---

## Step 2 — yellow Windows: fetch new wheels + bundle

> **First-time / deps-changed:** full payload (with wheels).
> **Code-only iteration (no requirements.txt change):** use `--no-wheels` — skips the 70 MB wheel bundle. Red zone reuses wheels from the prior deploy. See "Code-only iteration" section at the bottom.

> **One-time after this commit lands:** yellow's working tree may have CRLF-converted `*.sh` files from before `.gitattributes` existed. Run this once to renormalize:
> ```bash
> git pull
> git add --renormalize .
> git checkout -- .
> ```

```bash
cd simkit
git pull
python scripts/download_wheels.py --clean
python scripts/make_payload.py
```

Expected new wheels in `vendor/wheels/` after `download_wheels.py`:
- `PyQt5-5.15.9-cp37-abi3-manylinux_2_17_x86_64.whl`        (8.4 MB)
- `pyqt5_qt5-5.15.18-py3-none-manylinux2014_x86_64.whl`     (61 MB)
- `pyqt5_sip-12.18.0-cp311-cp311-manylinux_2_5_x86_64.manylinux1_x86_64.whl`  (277 KB)
- `qtawesome-1.4.2-py3-none-any.whl`                        (2.6 MB)
- `QtPy-2.4.3-py3-none-any.whl`                             (95 KB)
- `pytest_qt-4.5.0-py3-none-any.whl`                        (37 KB)
- `typing_extensions-4.15.0-py3-none-any.whl`               (45 KB)

`--clean` is recommended (wipes the stale 10-wheel cache so you get a
clean 17-wheel set; safe because pip's HTTP cache makes re-downloads
fast).

The resulting tarball goes into `dist/simkit_<date>_<sha>.tar.gz` plus a
sibling `.manifest.txt`. Transfer **both** to red zone.

---

## Step 3 — red Linux: unpack + redeploy

Assuming you put `SIMKIT_DEPLOYS_DIR` in `.bashrc` (or you're sitting in
`<DEPLOYS>/current` and using its scripts):

```bash
bash $SIMKIT_DEPLOYS_DIR/current/scripts/unpack_payload.sh /path/to/simkit_<date>_<sha>.tar.gz
cd $SIMKIT_DEPLOYS_DIR/simkit_<date>_<sha>
bash scripts/deploy_venv.sh
```

`deploy_venv.sh` runs the existing 4 smoke tests at the end (duckdb /
skillbridge / simkit / `pvt` CLI). PyQt5 is **not** in the smoke-test
list — verify it manually in step 4.

---

## Step 4 — red Linux: verify PyQt5 stack works

```bash
cd $SIMKIT_DEPLOYS_DIR/current
source .venv/bin/activate

# 1. PyQt5 imports
python -c "import PyQt5.QtWidgets; print('PyQt5 OK', PyQt5.QtCore.QT_VERSION_STR)"
python -c "import qtawesome; print('QtAwesome OK', qtawesome.__version__)"
python -c "import pytestqt; print('pytest-qt OK', pytestqt.__version__)"

# 2. pvt CLI still works
pvt --help

# 3. (After Phase 4 §3 lands) the gui subcommand
pvt gui --help
```

If any import fails, re-run `bash scripts/deploy_venv.sh --force` to
rebuild the venv from scratch.

---

## Code-only iteration (no requirements.txt change)

When you only changed Python / SKILL / docs / configs — no `requirements*.txt`
diff — skip the 70 MB wheel bundle:

```bash
# Yellow
cd simkit && git pull
python scripts/make_payload.py --no-wheels
# → dist/simkit_<date>_<sha>_code.tar.gz  (few MB, no vendor/wheels/)
```

```bash
# Red
bash $SIMKIT_DEPLOYS_DIR/current/scripts/unpack_payload.sh /path/to/simkit_<...>_code.tar.gz
# unpack auto-copies wheels from <deploys>/current/vendor/wheels/ into the new deploy.
cd $SIMKIT_DEPLOYS_DIR/simkit_<...>_code
bash scripts/deploy_venv.sh
```

Hard requirement: a prior **full-payload** deploy must exist as `<deploys>/current/`.
If not, `unpack_payload.sh` exits with code 5 and tells you to do a full deploy first.

---

## Notes

- **Display required for actually launching `pvt gui`** — Qt needs a
  working X display (`$DISPLAY` set) or VNC session. The imports above
  succeed without one; only `QApplication()` instantiation needs the
  display.
- **No changes to the 3 deploy scripts were needed.** They read from
  `requirements.lock.txt` and `vendor/wheels/` generically — adding
  wheels to the lock is the only required action.
- **Line endings:** `.gitattributes` forces LF on `*.sh` / `*.py` so
  yellow Windows can't bake CRLF into the tarball. `unpack_payload.sh`
  also strips CRLF defensively post-extract — double safety net.
- **Optional cleanup later:** `bash $SIMKIT_DEPLOYS_DIR/current/scripts/cleanup_deploys.sh --keep 3 --dry-run`
  to preview removing older Phase 3 deploys.
