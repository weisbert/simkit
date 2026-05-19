#!/usr/bin/env python3
"""Bundle simkit source + offline wheels + deploy scripts into a tarball.

Run on yellow Windows after scripts/download_wheels.py.
Cross-platform: uses Python's tarfile module so it works the same on
Windows and Linux (no dependency on tar.exe).

Output:
  dist/simkit_<utc-date>_<git-sha>.tar.gz
  dist/simkit_<utc-date>_<git-sha>.manifest.txt

The manifest holds the SHA256 + size + build metadata so unpack_payload.sh
can verify integrity before extracting on red zone.

Usage:
    python scripts/make_payload.py                          # default name
    python scripts/make_payload.py --name simkit_handoff    # custom name
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"

# Top-level entries to include in the tarball. Anything else at repo
# root is excluded (untracked logs, IDE files, etc.). Order is
# preserved in the resulting archive listing.
INCLUDE = [
    "python",
    "skill",
    "config",
    "tests",
    "docs",
    "examples",
    "vendor/wheels",
    "scripts",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock.txt",
    "pyproject.toml",
    "README.md",
    "PROJECT_STATE.md",
    "DECISIONS.md",
    "TODO.md",
    "PHASE_PLAN.md",
]

# Glob patterns excluded INSIDE any included path. Matched against both
# the full member path AND the basename to catch nested `__pycache__/`
# and similar.
EXCLUDE_PATTERNS = [
    "__pycache__",
    "__pycache__/*",
    ".pytest_cache",
    ".pytest_cache/*",
    ".mypy_cache",
    ".mypy_cache/*",
    ".ruff_cache",
    ".ruff_cache/*",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "Thumbs.db",
    ".venv",
    ".venv/*",
    ".git",
    ".git/*",
    ".vscode",
    ".idea",
    "*.duckdb",
    "*.duckdb.wal",
    "*.db",
    "*.sqlite*",
    "skillbridge_*.log",
    "node_modules",
    "*.swp",
    "*~",
    ".pvtproject.local",
]


def get_short_sha() -> str:
    """Return the 7-char git SHA, or 'nogit' if not in a git checkout."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return sha or "nogit"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def should_exclude(name: str) -> bool:
    """Return True if any EXCLUDE_PATTERN matches name or its basename."""
    base = os.path.basename(name)
    for pat in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(base, pat):
            return True
        # Also match the pattern as a path segment, e.g. "foo/__pycache__/bar"
        if "/" in name:
            parts = name.split("/")
            for part in parts:
                if fnmatch.fnmatch(part, pat):
                    return True
    return False


def make_filter(verbose: bool = False):
    """Return a tarfile filter function that strips excluded members."""
    def _filter(tarinfo: tarfile.TarInfo):
        if should_exclude(tarinfo.name):
            if verbose:
                print(f"  excluded: {tarinfo.name}")
            return None
        return tarinfo
    return _filter


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DIST_DIR,
        help=f"Output directory (default: {DIST_DIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--name",
        help="Override default 'simkit_<date>_<sha>' filename stem",
    )
    parser.add_argument(
        "--allow-empty-wheels",
        action="store_true",
        help="Skip the 'vendor/wheels must contain *.whl' precheck",
    )
    parser.add_argument(
        "--no-wheels",
        action="store_true",
        help=(
            "Skip vendor/wheels entirely — produces a code-only payload "
            "(~MB, not ~70 MB). Red zone reuses wheels from <deploys>/current/. "
            "Tarball name gets '_code' suffix."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every excluded member during packing",
    )
    args = parser.parse_args()

    # Precheck: vendor/wheels must have content (unless overridden OR --no-wheels)
    wheels_dir = REPO_ROOT / "vendor" / "wheels"
    if not args.allow_empty_wheels and not args.no_wheels:
        if not wheels_dir.exists() or not list(wheels_dir.glob("*.whl")):
            print(
                f"ERROR: {wheels_dir.relative_to(REPO_ROOT)} is empty or missing.\n"
                f"Run scripts/download_wheels.py first, or pass "
                f"--allow-empty-wheels to override, or --no-wheels for a "
                f"code-only payload (reuses wheels from prior red-zone deploy).",
                file=sys.stderr,
            )
            return 2

    # Filter INCLUDE: drop vendor/wheels when --no-wheels
    include_paths = [p for p in INCLUDE if not (args.no_wheels and p == "vendor/wheels")]

    args.output_dir.mkdir(parents=True, exist_ok=True)

    sha = get_short_sha()
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    default_stem = f"simkit_{date}_{sha}_code" if args.no_wheels else f"simkit_{date}_{sha}"
    stem = args.name or default_stem
    archive_path = args.output_dir / f"{stem}.tar.gz"
    manifest_path = args.output_dir / f"{stem}.manifest.txt"

    mode_banner = "CODE-ONLY (no wheels)" if args.no_wheels else "FULL (with wheels)"
    print(f"[make_payload] Building {archive_path.relative_to(REPO_ROOT)}  [{mode_banner}]")

    # Pack into a temporary file first, then rename — avoids leaving a
    # half-written tarball on disk if the build is interrupted.
    tmp_path = archive_path.with_suffix(".tar.gz.tmp")
    members_count = 0

    filt = make_filter(verbose=args.verbose)
    with tarfile.open(tmp_path, "w:gz") as tar:
        for path_str in include_paths:
            src = REPO_ROOT / path_str
            if not src.exists():
                print(f"  (skipped — not found: {path_str})")
                continue
            arcname = f"{stem}/{path_str}"
            # Capture member count via a counting filter wrapper.
            def counting_filter(ti, _base=filt):
                nonlocal members_count
                rv = _base(ti)
                if rv is not None:
                    members_count += 1
                return rv

            tar.add(src, arcname=arcname, filter=counting_filter)
            print(f"  included: {path_str}")

    tmp_path.rename(archive_path)

    size_bytes = archive_path.stat().st_size
    size_mb = size_bytes / 1024 / 1024
    sha256 = sha256_of_file(archive_path)

    with manifest_path.open("w") as f:
        f.write(f"Payload:    {archive_path.name}\n")
        f.write(f"Mode:       {'code-only' if args.no_wheels else 'full'}\n")
        f.write(f"Size:       {size_bytes} bytes ({size_mb:.2f} MB)\n")
        f.write(f"SHA256:     {sha256}\n")
        f.write(f"Members:    {members_count}\n")
        f.write(f"Built UTC:  {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        f.write(f"Git SHA:    {sha}\n")
        f.write(f"Builder:    {os.environ.get('USER', 'unknown')}@"
                f"{os.uname().nodename if hasattr(os, 'uname') else 'unknown'}\n")

    print()
    print(f"[make_payload] Done.  [{mode_banner}]")
    print(f"  Archive:  {archive_path}")
    print(f"  Size:     {size_mb:.2f} MB ({members_count} members)")
    print(f"  SHA256:   {sha256}")
    print(f"  Manifest: {manifest_path}")
    print()
    print("Next steps:")
    print(f"  1. Transfer BOTH files to red zone:")
    print(f"       {archive_path.name}")
    print(f"       {manifest_path.name}")
    print(f"  2. On red zone:")
    print(f"       bash scripts/unpack_payload.sh {archive_path.name}")
    if args.no_wheels:
        print()
        print("  NOTE: code-only payload — unpack_payload.sh will copy wheels")
        print("        from <deploys>/current/vendor/wheels/ automatically.")
        print("        Requires a prior full-payload deploy to exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
