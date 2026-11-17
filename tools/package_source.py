#!/usr/bin/env python3
"""Create a source-only ZIP using the same explicit publishable-tree checks as CI."""

import argparse
from pathlib import Path
import zipfile
from check_release import ROOT, check


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    files = check()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(
                file, "harmony-android-migrate/" + file.relative_to(ROOT).as_posix()
            )
    with zipfile.ZipFile(args.out) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
    print("Source package:", args.out.resolve())


if __name__ == "__main__":
    main()
