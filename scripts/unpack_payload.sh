#!/usr/bin/env bash
# Unpack a simkit payload tarball.
# Verifies SHA256 against the .manifest.txt sibling if present.
#
# Usage:
#   bash <deploys>/<deploy>/scripts/unpack_payload.sh <payload.tar.gz> [target-dir]
#
# Target dir resolution (first match wins):
#   1. Explicit 2nd arg
#   2. $SIMKIT_DEPLOYS_DIR env var
#   3. Auto-detect: 2 levels up from this script's own dir
#      (assumes script lives at <deploys-dir>/<deploy>/scripts/)
#   4. Current working directory (last resort)
#
# Exit codes:
#   0  ok
#   1  usage error
#   2  tarball file not found
#   3  SHA256 mismatch
#   4  extraction failed
#   5  code-only payload, no shared venv yet, and no wheels to seed one

set -euo pipefail

usage() {
    cat <<EOF >&2
Usage: $0 <payload.tar.gz> [target-dir]

  payload.tar.gz  Path to the simkit payload archive built by make_payload.py
  target-dir      Where to extract into; if omitted, auto-detected:
                  - \$SIMKIT_DEPLOYS_DIR env var if set
                  - else, the dir containing this script's deploy
                    (i.e. <deploys>/<deploy>/scripts/X.sh → <deploys>)
                  - else, current working directory

Examples:
  # Iteration path — auto-detect from this script's location
  bash <deploys>/current/scripts/unpack_payload.sh ~/new.tar.gz

  # First deploy / one-off — explicit target
  bash unpack_payload.sh ~/new.tar.gz /path/to/my/deploys/

  # Or via env var (e.g. in .bashrc):
  export SIMKIT_DEPLOYS_DIR=/home/me/workarea/simkit_deploys
  bash unpack_payload.sh ~/new.tar.gz

The script looks for a sibling <payload>.manifest.txt and verifies the
SHA256 if found. Missing manifest = skipped verification with a warning.

Exit codes: 0 ok / 1 usage / 2 not found / 3 checksum / 4 extract / 5 no shared venv and no wheels to seed one
EOF
    exit 1
}

[[ $# -ge 1 ]] || usage
[[ $# -le 2 ]] || usage

TARBALL="$1"

# Resolve target dir per the precedence chain documented in the header.
if [[ $# -ge 2 ]]; then
    TARGET="$2"
elif [[ -n "${SIMKIT_DEPLOYS_DIR:-}" ]]; then
    TARGET="$SIMKIT_DEPLOYS_DIR"
else
    # Script is at <deploys>/<deploy>/scripts/unpack_payload.sh — go up 2.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    AUTO_DEPLOYS="$(dirname "$(dirname "$SCRIPT_DIR")")"
    # Sanity check: if AUTO_DEPLOYS looks like a real deploys-dir
    # (i.e. has a 'current' symlink OR a simkit_*/ sibling), use it.
    # Otherwise fall back to cwd.
    if [[ -L "$AUTO_DEPLOYS/current" ]] \
            || compgen -G "$AUTO_DEPLOYS/simkit_*" > /dev/null 2>&1; then
        TARGET="$AUTO_DEPLOYS"
    else
        TARGET="$(pwd)"
    fi
fi

if [[ ! -f "$TARBALL" ]]; then
    echo "ERROR: tarball not found: $TARBALL" >&2
    exit 2
fi

# Resolve to absolute paths for clean logging
TARBALL="$(realpath "$TARBALL")"
TARGET_PARENT="$(dirname "$TARGET")"
mkdir -p "$TARGET_PARENT"
mkdir -p "$TARGET"
TARGET="$(realpath "$TARGET")"

# Verify checksum if manifest exists
MANIFEST="${TARBALL%.tar.gz}.manifest.txt"
if [[ -f "$MANIFEST" ]]; then
    echo "[unpack] Verifying SHA256 against $MANIFEST"
    EXPECTED="$(grep -oE "SHA256:[[:space:]]+[a-f0-9]+" "$MANIFEST" \
                | awk '{print $2}')"
    if [[ -z "$EXPECTED" ]]; then
        echo "WARNING: no SHA256 field found in manifest; skipping verification" >&2
    else
        ACTUAL="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
        if [[ "$EXPECTED" != "$ACTUAL" ]]; then
            echo "ERROR: SHA256 mismatch" >&2
            echo "  expected: $EXPECTED" >&2
            echo "  actual:   $ACTUAL" >&2
            exit 3
        fi
        echo "[unpack] SHA256 OK"
    fi
else
    echo "[unpack] WARNING: no sibling manifest found; integrity NOT verified" >&2
    echo "[unpack]   (looked for: $MANIFEST)" >&2
fi

# Identify the top-level dir inside the tarball (used to print the
# final extracted path to the user).
#
# `tar | head -1` + pipefail makes tar see SIGPIPE and exit non-zero;
# read line-by-line via process substitution instead.
TOP_DIR=""
while IFS= read -r line; do
    TOP_DIR="${line%%/*}"
    break
done < <(tar -tzf "$TARBALL" 2>/dev/null)

if [[ -z "$TOP_DIR" ]]; then
    echo "ERROR: could not read tarball table-of-contents" >&2
    exit 4
fi

EXTRACT_PATH="$TARGET/$TOP_DIR"
if [[ -d "$EXTRACT_PATH" ]]; then
    echo "WARNING: $EXTRACT_PATH already exists; existing files will be overwritten" >&2
fi

echo "[unpack] Extracting to $TARGET"
tar -xzf "$TARBALL" -C "$TARGET"

# Defensive: strip CRLF from text scripts. Belt-and-suspenders behind
# .gitattributes — if yellow zone ever bakes CRLF back in (stale working
# tree before .gitattributes lands), this keeps red zone unblocked.
if [[ -d "$EXTRACT_PATH/scripts" ]]; then
    find "$EXTRACT_PATH/scripts" -type f \( -name "*.sh" -o -name "*.py" \) \
        -exec sed -i 's/\r$//' {} +
fi

# Make scripts executable (tarfile preserves bits but defensively chmod)
if [[ -d "$EXTRACT_PATH/scripts" ]]; then
    chmod +x "$EXTRACT_PATH"/scripts/*.sh 2>/dev/null || true
    chmod +x "$EXTRACT_PATH"/scripts/*.py 2>/dev/null || true
fi

# Code-only payload — wheels missing OR empty in the extracted tree.
#
# The shared venv (<deploys>/venv) holds the installed deps and is reused
# across code-only deploys, so a code-only payload needs NO wheels once
# that venv exists. Wheels are only seeded when the shared venv is not
# there yet (first deploy / venv rebuild) — copied from the prior deploy.
WHEELS_DIR="$EXTRACT_PATH/vendor/wheels"
SHARED_VENV="$TARGET/venv"
if [[ ! -d "$WHEELS_DIR" ]] || ! compgen -G "$WHEELS_DIR/*.whl" > /dev/null; then
    if [[ -d "$SHARED_VENV" ]]; then
        echo "[unpack] Code-only payload — shared venv $SHARED_VENV exists;"
        echo "[unpack]   no wheels needed (deploy_venv.sh reuses the venv)."
    else
        PRIOR_WHEELS="$TARGET/current/vendor/wheels"
        if [[ -d "$PRIOR_WHEELS" ]] && compgen -G "$PRIOR_WHEELS/*.whl" > /dev/null; then
            echo "[unpack] Code-only payload, no shared venv yet — seeding wheels."
            mkdir -p "$WHEELS_DIR"
            # Use cp -L to dereference any symlinks in the source.
            cp -L "$PRIOR_WHEELS"/*.whl "$WHEELS_DIR/"
            WHEEL_COUNT=$(ls "$WHEELS_DIR"/*.whl 2>/dev/null | wc -l)
            echo "[unpack]   copied $WHEEL_COUNT wheels from $PRIOR_WHEELS"
        else
            echo "ERROR: code-only payload, no shared venv and no wheels to" >&2
            echo "       seed one. Do a full payload deploy first (without" >&2
            echo "       --no-wheels) to build <deploys>/venv." >&2
            exit 5
        fi
    fi
fi

echo ""
echo "[unpack] Done."
echo "  Extracted to: $EXTRACT_PATH"
echo ""
echo "Next step:"
echo "  cd $EXTRACT_PATH && bash scripts/deploy_venv.sh"
