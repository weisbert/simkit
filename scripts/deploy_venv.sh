#!/usr/bin/env bash
# Create a .venv and install simkit + deps offline from vendor/wheels/.
# Designed for red zone Linux, no internet required.
#
# Run from the unpacked simkit deployment directory:
#   cd ~/simkit_deploys/simkit_YYYYMMDD_SHA
#   bash scripts/deploy_venv.sh
#
# On success, atomically points ../current -> this deploy dir so the
# "currently active" deployment has a stable path. Use --no-current to
# skip this step (e.g. for a trial deploy you don't want to activate
# yet).
#
# Env vars:
#   PYTHON     Python interpreter to use (default: python3)
#   VENV_DIR   Where to put the venv (default: .venv)
#
# Flags:
#   --force        Remove existing .venv before creating
#   --no-smoke     Skip post-install smoke tests
#   --no-current   Skip updating ../current symlink
#
# Exit codes:
#   0 ok
#   1 missing prereqs
#   2 venv already exists (use --force)
#   3 install failed
#   4 smoke test failed

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
FORCE=0
NO_SMOKE=0
NO_CURRENT=0

for arg in "$@"; do
    case "$arg" in
        --force)      FORCE=1 ;;
        --no-smoke)   NO_SMOKE=1 ;;
        --no-current) NO_CURRENT=1 ;;
        -h|--help)
            grep '^#' "$0" | grep -v '^#!' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown arg: $arg" >&2
            exit 1
            ;;
    esac
done

# --- Prereq checks ---

if ! command -v "$PYTHON" > /dev/null; then
    echo "ERROR: $PYTHON not found on PATH" >&2
    echo "       (override with PYTHON=/abs/path)" >&2
    exit 1
fi

PYVER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[deploy_venv] Using $PYTHON (Python $PYVER)"

WHEELS_DIR="vendor/wheels"
if [[ ! -d "$WHEELS_DIR" ]] || [[ -z "$(ls -A "$WHEELS_DIR" 2>/dev/null)" ]]; then
    echo "ERROR: $WHEELS_DIR is missing or empty" >&2
    echo "       Expected location: $REPO_DIR/$WHEELS_DIR/*.whl" >&2
    exit 1
fi

LOCK="requirements.lock.txt"
if [[ ! -f "$LOCK" ]]; then
    echo "ERROR: $LOCK not found in $REPO_DIR" >&2
    exit 1
fi

# --- Handle existing .venv ---

if [[ -d "$VENV_DIR" ]]; then
    if [[ "$FORCE" == "1" ]]; then
        echo "[deploy_venv] --force: removing existing $VENV_DIR"
        rm -rf "$VENV_DIR"
    else
        echo "ERROR: $VENV_DIR already exists" >&2
        echo "       Use --force to wipe and recreate, or set VENV_DIR=other/path" >&2
        exit 2
    fi
fi

# --- Create venv ---

echo "[deploy_venv] Creating venv at $VENV_DIR (no --system-site-packages: full isolation)"
"$PYTHON" -m venv "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Verify we're actually in the venv (defensive — catch broken venv creation)
ACTIVE="$(python -c 'import sys; print(sys.prefix)')"
EXPECTED="$REPO_DIR/$VENV_DIR"
if [[ "$ACTIVE" != "$EXPECTED" ]]; then
    echo "ERROR: venv activation failed" >&2
    echo "  expected sys.prefix: $EXPECTED" >&2
    echo "  actual sys.prefix:   $ACTIVE" >&2
    exit 3
fi

# --- Install deps offline ---

echo "[deploy_venv] Installing locked deps from $LOCK (offline, --find-links=$WHEELS_DIR)"
if ! pip install --no-index --find-links="$WHEELS_DIR" -r "$LOCK"; then
    echo "ERROR: pip install failed" >&2
    exit 3
fi

echo "[deploy_venv] Installing simkit in editable mode"
# --no-build-isolation: PEP 517 by default spins up a fresh build env
# and pip-installs setuptools+wheel into it, which fails offline. The
# venv we already created has setuptools+wheel from ensurepip, so we
# bypass isolation and use those directly.
if ! pip install --no-index --find-links="$WHEELS_DIR" --no-deps \
        --no-build-isolation -e .; then
    echo "ERROR: editable install failed" >&2
    exit 3
fi

# --- Patch activate scripts: prepend bundled PyQt5 Qt5 lib path ---
#
# Why: red-zone EDA farms expose Cadence's own Qt5 via LD_LIBRARY_PATH
# (e.g. /software/public/qt/5.15.3_xcb/lib). At PyQt5 import time the
# Linux loader picks up Cadence's older Qt5 instead of the wheel's
# bundled 5.15.18, and dies with:
#   "symbol _ZdlPvm, version Qt_5 not defined in file libQt5Core.so.5"
# Prepending the wheel's lib dir to LD_LIBRARY_PATH at activation time
# forces the loader to find the correct Qt5 first.

QT5_LIB_REL="$VENV_DIR/lib/python3.11/site-packages/PyQt5/Qt5/lib"
if [[ -d "$REPO_DIR/$QT5_LIB_REL" ]]; then
    echo "[deploy_venv] Patching activate scripts: prepend PyQt5 Qt5 lib to LD_LIBRARY_PATH"

    cat >> "$VENV_DIR/bin/activate" <<'EOF'

# Added by simkit deploy_venv.sh: prepend bundled PyQt5 Qt5 libs so
# Cadence's older Qt5 (in LD_LIBRARY_PATH) doesn't shadow them.
export LD_LIBRARY_PATH="$VIRTUAL_ENV/lib/python3.11/site-packages/PyQt5/Qt5/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
EOF

    cat >> "$VENV_DIR/bin/activate.csh" <<'EOF'

# Added by simkit deploy_venv.sh: prepend bundled PyQt5 Qt5 libs so
# Cadence's older Qt5 (in LD_LIBRARY_PATH) doesn't shadow them.
if ($?LD_LIBRARY_PATH) then
    setenv LD_LIBRARY_PATH "$VIRTUAL_ENV/lib/python3.11/site-packages/PyQt5/Qt5/lib:$LD_LIBRARY_PATH"
else
    setenv LD_LIBRARY_PATH "$VIRTUAL_ENV/lib/python3.11/site-packages/PyQt5/Qt5/lib"
endif
EOF

    # Also export in our own shell so the smoke test below sees the right libs.
    export LD_LIBRARY_PATH="$REPO_DIR/$QT5_LIB_REL${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# --- Smoke tests ---

if [[ "$NO_SMOKE" == "0" ]]; then
    echo ""
    echo "[deploy_venv] Smoke tests:"

    smoke_fail=0

    if python -c "import duckdb; print('  duckdb       OK,', duckdb.__version__)" 2>/dev/null; then
        :
    else
        echo "  duckdb       FAIL"
        smoke_fail=1
    fi

    if python -c "import skillbridge; print('  skillbridge  OK')" 2>/dev/null; then
        :
    else
        echo "  skillbridge  FAIL"
        smoke_fail=1
    fi

    if python -c "import simkit; print('  simkit       OK')" 2>/dev/null; then
        :
    else
        echo "  simkit       FAIL"
        smoke_fail=1
    fi

    if command -v pvt > /dev/null; then
        echo "  pvt CLI      OK ($(which pvt))"
    else
        echo "  pvt CLI      FAIL — not on PATH"
        smoke_fail=1
    fi

    # PyQt5 import test — exercises the LD_LIBRARY_PATH patch above.
    # Only present if the gui extras are installed (i.e. wheels were
    # bundled). Skip silently otherwise.
    if [[ -d "$REPO_DIR/$QT5_LIB_REL" ]]; then
        if python -c "from PyQt5.QtWidgets import QApplication" 2>/dev/null; then
            echo "  PyQt5        OK"
        else
            echo "  PyQt5        FAIL — run \`python -c 'from PyQt5.QtWidgets import QApplication'\` for the real error"
            smoke_fail=1
        fi
    fi

    if [[ "$smoke_fail" == "1" ]]; then
        echo ""
        echo "ERROR: smoke tests failed" >&2
        exit 4
    fi
fi

# --- Update 'current' symlink (../current -> this deploy) ---

CURRENT_LINK=""
if [[ "$NO_CURRENT" == "0" ]]; then
    DEPLOYS_PARENT="$(dirname "$REPO_DIR")"
    CURRENT_LINK="$DEPLOYS_PARENT/current"

    # `ln -sfn` atomically replaces the existing symlink. `-f` removes
    # an existing link/file; `-n` is critical — without it, if `current`
    # already points to a directory, ln descends INTO that dir and
    # creates a nested symlink (classic ln footgun).
    if ln -sfn "$REPO_DIR" "$CURRENT_LINK"; then
        echo ""
        echo "[deploy_venv] Updated symlink: $CURRENT_LINK -> $(basename "$REPO_DIR")"
    else
        echo ""
        echo "WARNING: failed to update $CURRENT_LINK symlink (deploy still OK)" >&2
        CURRENT_LINK=""
    fi
fi

echo ""
echo "[deploy_venv] Done."
echo "  venv: $REPO_DIR/$VENV_DIR"

if [[ -n "$CURRENT_LINK" ]]; then
    echo ""
    echo "Activate via the stable 'current' path (recommended):"
    echo "  bash:  cd $CURRENT_LINK && source $VENV_DIR/bin/activate"
    echo "  csh:   cd $CURRENT_LINK && source $VENV_DIR/bin/activate.csh"
else
    echo ""
    echo "Activate via the absolute deploy path:"
    echo "  bash:  cd $REPO_DIR && source $VENV_DIR/bin/activate"
    echo "  csh:   cd $REPO_DIR && source $VENV_DIR/bin/activate.csh"
fi

echo ""
echo "Try:"
echo "  pvt --help"
