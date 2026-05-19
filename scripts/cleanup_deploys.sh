#!/usr/bin/env bash
# Prune old simkit deployment directories, keeping the N most recent.
# Always protects the directory that the `current` symlink points to,
# even if that directory wouldn't fall within the top N by mtime.
#
# Usage:
#   bash scripts/cleanup_deploys.sh [--keep N] [--dry-run] [--deploys-dir DIR]
#
# Flags:
#   --keep N         Keep this many most-recent deploys (default: 3)
#   --dry-run        Print what would be deleted, don't actually delete
#   --deploys-dir D  Where deploys live (default: ~/simkit_deploys)
#   --help           Show this help
#
# Exit codes:
#   0 ok (something deleted or nothing to do)
#   1 usage / missing dir
#   2 invalid --keep value

set -euo pipefail

KEEP=3
DRY_RUN=0
DEPLOYS_DIR="${HOME}/simkit_deploys"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep)
            KEEP="${2:-}"
            shift 2
            ;;
        --keep=*)
            KEEP="${1#*=}"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --deploys-dir)
            DEPLOYS_DIR="${2:-}"
            shift 2
            ;;
        --deploys-dir=*)
            DEPLOYS_DIR="${1#*=}"
            shift
            ;;
        -h|--help)
            grep '^#' "$0" | grep -v '^#!' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

if ! [[ "$KEEP" =~ ^[0-9]+$ ]] || [[ "$KEEP" -lt 1 ]]; then
    echo "ERROR: --keep must be a positive integer, got '$KEEP'" >&2
    exit 2
fi

if [[ ! -d "$DEPLOYS_DIR" ]]; then
    echo "ERROR: deploys dir not found: $DEPLOYS_DIR" >&2
    echo "       (override with --deploys-dir /path/to/dir)" >&2
    exit 1
fi

DEPLOYS_DIR="$(cd "$DEPLOYS_DIR" && pwd)"   # canonicalize

# Identify the 'current' deploy (if symlink exists + valid)
CURRENT_TARGET=""
CURRENT_LINK="$DEPLOYS_DIR/current"
if [[ -L "$CURRENT_LINK" ]]; then
    # readlink -f resolves the chain; -e ensures the target exists.
    # If the link is broken (target deleted), we treat it as no current.
    if CURRENT_TARGET="$(readlink -e "$CURRENT_LINK")"; then
        :
    else
        echo "[cleanup] WARNING: 'current' symlink is broken (target missing)" >&2
        CURRENT_TARGET=""
    fi
fi

# Collect all simkit_<date>_<sha> directories in DEPLOYS_DIR, sorted
# newest first by mtime.
mapfile -t ALL_DEPLOYS < <(
    find "$DEPLOYS_DIR" -mindepth 1 -maxdepth 1 -type d -name 'simkit_*' \
        -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn \
        | awk '{print $2}'
)

if [[ ${#ALL_DEPLOYS[@]} -eq 0 ]]; then
    echo "[cleanup] No simkit_* deployments found under $DEPLOYS_DIR"
    exit 0
fi

echo "[cleanup] Found ${#ALL_DEPLOYS[@]} deploy(s) in $DEPLOYS_DIR (newest first):"
for d in "${ALL_DEPLOYS[@]}"; do
    marker=""
    if [[ -n "$CURRENT_TARGET" && "$d" == "$CURRENT_TARGET" ]]; then
        marker="  (current)"
    fi
    size="$(du -sh "$d" 2>/dev/null | cut -f1)"
    printf "  %-60s %5s%s\n" "$(basename "$d")" "$size" "$marker"
done

# Decide which to keep:
#   - The top KEEP by mtime
#   - Plus the one current points to (always)
declare -A KEEP_SET
i=0
for d in "${ALL_DEPLOYS[@]}"; do
    if [[ "$i" -lt "$KEEP" ]]; then
        KEEP_SET["$d"]=1
    fi
    i=$((i+1))
done
if [[ -n "$CURRENT_TARGET" ]]; then
    KEEP_SET["$CURRENT_TARGET"]=1
fi

# Build deletion list
DELETE_LIST=()
for d in "${ALL_DEPLOYS[@]}"; do
    if [[ -z "${KEEP_SET[$d]:-}" ]]; then
        DELETE_LIST+=("$d")
    fi
done

echo ""
if [[ ${#DELETE_LIST[@]} -eq 0 ]]; then
    echo "[cleanup] Nothing to delete (--keep $KEEP and 'current' both satisfied)."
    exit 0
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[cleanup] DRY RUN — would delete ${#DELETE_LIST[@]} deploy(s):"
else
    echo "[cleanup] Deleting ${#DELETE_LIST[@]} deploy(s):"
fi

TOTAL_FREED=0
for d in "${DELETE_LIST[@]}"; do
    size_kb="$(du -sk "$d" 2>/dev/null | cut -f1 || echo 0)"
    TOTAL_FREED=$((TOTAL_FREED + size_kb))
    size_human="$(du -sh "$d" 2>/dev/null | cut -f1)"
    if [[ "$DRY_RUN" == "1" ]]; then
        printf "  WOULD DELETE  %-60s %5s\n" "$(basename "$d")" "$size_human"
    else
        printf "  deleting      %-60s %5s\n" "$(basename "$d")" "$size_human"
        rm -rf "$d"
    fi
done

total_freed_human="$(echo "$TOTAL_FREED" | awk '{
    if ($1 > 1048576) printf "%.1f GB", $1/1048576
    else if ($1 > 1024) printf "%.1f MB", $1/1024
    else printf "%d KB", $1
}')"

echo ""
if [[ "$DRY_RUN" == "1" ]]; then
    echo "[cleanup] DRY RUN done. Would free: $total_freed_human"
    echo "          Re-run without --dry-run to actually delete."
else
    echo "[cleanup] Done. Freed: $total_freed_human"
fi
