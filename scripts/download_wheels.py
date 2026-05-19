#!/usr/bin/env python3
"""Download Linux x86_64 manylinux wheels for offline install on red zone.

Run on the yellow Windows machine (or any machine with internet).
Reads ``requirements.lock.txt`` and downloads each pinned package as a
manylinux wheel into ``vendor/wheels/``.

Important: by default ``pip download`` on Windows fetches Windows wheels.
The ``--platform`` / ``--implementation`` / ``--abi`` flags below force
pip to fetch the Linux wheel variant instead, which is what red zone
needs.

Incremental: pip's own HTTP cache makes repeat runs fast (no re-download
from PyPI). The destination dir is NOT wiped unless ``--clean`` is set.

Usage:
    python scripts/download_wheels.py                  # default
    python scripts/download_wheels.py --clean          # wipe first
    python scripts/download_wheels.py --python-version 3.12
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO_ROOT / "requirements.lock.txt"
DEFAULT_DEST = REPO_ROOT / "vendor" / "wheels"
DEFAULT_PYTHON_VERSION = "3.11"
DEFAULT_ABI = "cp311"
DEFAULT_IMPLEMENTATION = "cp"

# Lowest-common-denominator: red zone is glibc 2.17 (RHEL7-era EDA farm).
# Both names below mean the same baseline (manylinux2014 == manylinux_2_17).
# NEVER add a newer baseline (e.g. manylinux_2_28) here — pip would
# accept too-new wheels that won't install on red. If you must support a
# package that no longer ships 2_17 wheels, pin its version DOWN in the
# lock to the last release that does, OR custom-build the wheel.
DEFAULT_PLATFORMS = [
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK,
        help=f"Lock file to read (default: {DEFAULT_LOCK.name})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Destination dir for wheels (default: {DEFAULT_DEST.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--python-version",
        default=DEFAULT_PYTHON_VERSION,
        help=f"Target Python version (default: {DEFAULT_PYTHON_VERSION})",
    )
    parser.add_argument(
        "--abi",
        default=DEFAULT_ABI,
        help=f"Target ABI tag (default: {DEFAULT_ABI})",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe destination before download (default: incremental)",
    )
    args = parser.parse_args()

    if not args.lock.exists():
        print(f"ERROR: lock file not found: {args.lock}", file=sys.stderr)
        return 2

    if args.clean and args.dest.exists():
        print(f"[download_wheels] --clean: removing {args.dest}")
        shutil.rmtree(args.dest)

    args.dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "pip", "download",
        "--dest", str(args.dest),
        "--python-version", args.python_version,
        "--implementation", DEFAULT_IMPLEMENTATION,
        "--abi", args.abi,
        "--only-binary=:all:",
        "-r", str(args.lock),
    ]
    for platform in DEFAULT_PLATFORMS:
        cmd.extend(["--platform", platform])

    print(f"[download_wheels] target: python={args.python_version} abi={args.abi}")
    print(f"[download_wheels] platforms: {', '.join(DEFAULT_PLATFORMS)}")
    print(f"[download_wheels] dest: {args.dest}")
    print(f"[download_wheels] running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(
            f"\nERROR: pip download exited with code {result.returncode}",
            file=sys.stderr,
        )
        return result.returncode

    wheels = sorted(args.dest.glob("*.whl"))
    total_bytes = sum(w.stat().st_size for w in wheels)
    total_mb = total_bytes / 1024 / 1024

    print(f"\n[download_wheels] {len(wheels)} wheels in {args.dest} "
          f"({total_mb:.1f} MB total):")
    for w in wheels:
        size_mb = w.stat().st_size / 1024 / 1024
        print(f"  {w.name}  ({size_mb:.2f} MB)")

    print(f"\n[download_wheels] Done. Next: python scripts/make_payload.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
