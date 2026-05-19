#!/usr/bin/env bash
# Unpack a simkit payload tarball on red zone Linux.
# Verifies SHA256 against the .manifest.txt sibling if present.
#
# Usage:
#   bash scripts/unpack_payload.sh <payload.tar.gz> [target-dir]
#
#   target-dir defaults to ~/simkit_deploys/
#
# Exit codes:
#   0  ok
#   1  usage error
#   2  tarball file not found
#   3  SHA256 mismatch
#   4  extraction failed

set -euo pipefail

usage() {
    cat <<EOF >&2
Usage: $0 <payload.tar.gz> [target-dir]

  payload.tar.gz  Path to the simkit payload archive built by make_payload.py
  target-dir      Directory to extract into (default: ~/simkit_deploys/)

The script looks for a sibling <payload>.manifest.txt and verifies the
SHA256 if found. Missing manifest = skipped verification with a warning.

Exit codes: 0 ok / 1 usage / 2 not found / 3 checksum / 4 extract fail
EOF
    exit 1
}

[[ $# -ge 1 ]] || usage
[[ $# -le 2 ]] || usage

TARBALL="$1"
TARGET="${2:-$HOME/simkit_deploys}"

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

# Make scripts executable (tarfile preserves bits but defensively chmod)
if [[ -d "$EXTRACT_PATH/scripts" ]]; then
    chmod +x "$EXTRACT_PATH"/scripts/*.sh 2>/dev/null || true
    chmod +x "$EXTRACT_PATH"/scripts/*.py 2>/dev/null || true
fi

echo ""
echo "[unpack] Done."
echo "  Extracted to: $EXTRACT_PATH"
echo ""
echo "Next step:"
echo "  cd $EXTRACT_PATH && bash scripts/deploy_venv.sh"
