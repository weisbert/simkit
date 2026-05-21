#!/usr/bin/env bash
# Activate a simkit code deploy against a SHARED, lock-keyed venv.
#
# The venv lives ONE level up at <DEPLOYS>/venv and is keyed to
# requirements.lock.txt. It is rebuilt ONLY when the lock changes (or
# --force). A code-only deploy just flips the <DEPLOYS>/current symlink:
# the venv imports simkit through a static .pth that follows `current`,
# so no pip / no reinstall happens when dependencies are unchanged.
#
# Run from the unpacked simkit deployment directory:
#   cd <DEPLOYS>/simkit_YYYYMMDD_SHA
#   bash scripts/deploy_venv.sh
#
# On success, atomically points <DEPLOYS>/current -> this deploy dir.
#
# Env vars:
#   PYTHON     Python interpreter to use (default: python3)
#   VENV_DIR   Shared venv location (default: <DEPLOYS>/venv)
#
# Flags:
#   --force        Rebuild the shared venv even if the lock is unchanged
#   --no-smoke     Skip post-install smoke tests
#   --no-current   Do not flip the <DEPLOYS>/current symlink
#
# Exit codes:
#   0 ok
#   1 missing prereqs
#   3 install failed
#   4 smoke test failed

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

DEPLOYS_DIR="$(dirname "$REPO_DIR")"
PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-$DEPLOYS_DIR/venv}"
CURRENT_LINK="$DEPLOYS_DIR/current"
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

LOCK="requirements.lock.txt"
if [[ ! -f "$LOCK" ]]; then
    echo "ERROR: $LOCK not found in $REPO_DIR" >&2
    exit 1
fi
LOCK_HASH="$(sha256sum "$LOCK" | cut -d' ' -f1)"
HASH_FILE="$VENV_DIR/.simkit_lockhash"

# --- Flip the 'current' symlink FIRST -------------------------------------
#
# The shared venv imports simkit via a static .pth that points at
# <DEPLOYS>/current/python. `current` must already point at THIS deploy
# before the smoke test runs, or the test would import the old code.

if [[ "$NO_CURRENT" == "0" ]]; then
    # `-n` is critical: without it, ln descends into an existing
    # `current` directory-symlink and nests a link inside it.
    if ln -sfn "$REPO_DIR" "$CURRENT_LINK"; then
        echo "[deploy_venv] current -> $(basename "$REPO_DIR")"
    else
        echo "ERROR: failed to update $CURRENT_LINK symlink" >&2
        exit 1
    fi
else
    echo "[deploy_venv] --no-current: leaving $CURRENT_LINK as-is"
fi

# --- Decide: rebuild the shared venv, or reuse it -------------------------

NEED_BUILD=0
BUILD_REASON=""
if [[ ! -d "$VENV_DIR" ]]; then
    NEED_BUILD=1
    BUILD_REASON="no shared venv at $VENV_DIR yet"
elif [[ "$FORCE" == "1" ]]; then
    NEED_BUILD=1
    BUILD_REASON="--force"
elif [[ ! -f "$HASH_FILE" ]] || [[ "$(cat "$HASH_FILE")" != "$LOCK_HASH" ]]; then
    NEED_BUILD=1
    BUILD_REASON="requirements.lock.txt changed since the venv was built"
fi

if [[ "$NEED_BUILD" == "1" ]]; then
    echo "[deploy_venv] Building the shared venv ($BUILD_REASON)"

    WHEELS_DIR="vendor/wheels"
    if [[ ! -d "$WHEELS_DIR" ]] || [[ -z "$(ls -A "$WHEELS_DIR" 2>/dev/null)" ]]; then
        echo "ERROR: a venv (re)build is needed but $WHEELS_DIR is empty" >&2
        echo "       Deploy a full (with-wheels) payload — a code-only" >&2
        echo "       payload cannot rebuild the venv." >&2
        exit 1
    fi

    rm -rf "$VENV_DIR"
    echo "[deploy_venv] Creating venv at $VENV_DIR (full isolation)"
    "$PYTHON" -m venv "$VENV_DIR"

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    ACTIVE="$(python -c 'import sys; print(sys.prefix)')"
    if [[ "$ACTIVE" != "$VENV_DIR" ]]; then
        echo "ERROR: venv activation failed" >&2
        echo "  expected sys.prefix: $VENV_DIR" >&2
        echo "  actual sys.prefix:   $ACTIVE" >&2
        exit 3
    fi

    echo "[deploy_venv] Installing locked deps from $LOCK (offline)"
    if ! pip install --no-index --find-links="$WHEELS_DIR" -r "$LOCK"; then
        echo "ERROR: pip install failed" >&2
        exit 3
    fi

    # simkit itself is NOT pip-installed. The venv finds it through a
    # static .pth that points at <DEPLOYS>/current/python — so flipping
    # `current` is all a code-only deploy needs.
    SITE_DIR="$VENV_DIR/lib/python$PYVER/site-packages"
    if [[ ! -d "$SITE_DIR" ]]; then
        SITE_DIR="$(python -c 'import site; print(site.getsitepackages()[0])')"
    fi
    echo "$CURRENT_LINK/python" > "$SITE_DIR/simkit_src.pth"
    echo "[deploy_venv] Wrote $SITE_DIR/simkit_src.pth -> $CURRENT_LINK/python"

    # `pvt` console entry — a path-independent wrapper (no editable install).
    cat > "$VENV_DIR/bin/pvt" <<'EOF'
#!/bin/sh
# simkit `pvt` CLI — runs the active deploy (via the venv's simkit_src.pth).
exec "$(dirname "$0")/python" -m simkit.cli "$@"
EOF
    chmod +x "$VENV_DIR/bin/pvt"

    # --- Patch activate scripts: prepend bundled PyQt5 Qt5 lib path ---
    #
    # Red-zone EDA farms expose Cadence's own Qt5 via LD_LIBRARY_PATH; at
    # PyQt5 import time the loader picks that older Qt5 over the wheel's
    # bundled 5.15.x and dies on a missing symbol. Prepending the wheel's
    # Qt5 lib dir at activation forces the loader to the correct one.
    QT5_LIB="$VENV_DIR/lib/python$PYVER/site-packages/PyQt5/Qt5/lib"
    if [[ -d "$QT5_LIB" ]]; then
        echo "[deploy_venv] Patching activate scripts: prepend PyQt5 Qt5 lib"
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
        export LD_LIBRARY_PATH="$QT5_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi

    echo "$LOCK_HASH" > "$HASH_FILE"
    echo "[deploy_venv] Shared venv built; lock hash recorded."
else
    echo "[deploy_venv] Shared venv is up to date (lock unchanged) — reusing,"
    echo "[deploy_venv]   no pip, no reinstall. This deploy is now active via"
    echo "[deploy_venv]   the 'current' symlink."
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    QT5_LIB="$VENV_DIR/lib/python$PYVER/site-packages/PyQt5/Qt5/lib"
    if [[ -d "$QT5_LIB" ]]; then
        export LD_LIBRARY_PATH="$QT5_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
fi

# --- Smoke tests ----------------------------------------------------------

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

    # simkit is resolved through the .pth -> current/python; this proves
    # the active deploy's code is importable.
    if python -c "import simkit; print('  simkit       OK,', simkit.__file__)" 2>/dev/null; then
        :
    else
        echo "  simkit       FAIL — check the simkit_src.pth / current symlink"
        smoke_fail=1
    fi

    if command -v pvt > /dev/null && pvt --help > /dev/null 2>&1; then
        echo "  pvt CLI      OK ($(command -v pvt))"
    else
        echo "  pvt CLI      FAIL"
        smoke_fail=1
    fi

    QT5_LIB="$VENV_DIR/lib/python$PYVER/site-packages/PyQt5/Qt5/lib"
    if [[ -d "$QT5_LIB" ]]; then
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

# --- Done -----------------------------------------------------------------

echo ""
echo "[deploy_venv] Done."
echo "  active deploy: $REPO_DIR"
echo "  shared venv:   $VENV_DIR"
echo ""
echo "Activate (the venv path is stable — it does not change per deploy):"
echo "  bash:  source $VENV_DIR/bin/activate"
echo "  csh:   source $VENV_DIR/bin/activate.csh"
echo ""
echo "Try:"
echo "  pvt --help"
