#!/usr/bin/env python3
"""Validate both signed variants and assemble a versioned, source-only release bundle."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
from build_android import VERSION, verify_apk
from check_release import ROOT, check


def validate_apk(path, variant, signer, certificate):
    verify_apk(signer, path, certificate)
    with zipfile.ZipFile(path) as archive:
        metadata = json.loads(archive.read("assets/hmos-migrate.json"))
        expected = {
            "package": "dev.hmos.migrate",
            "protocol": 1,
            "version": VERSION["__version__"],
            "version_code": VERSION["ANDROID_VERSION_CODE"],
            "variant": variant,
            "debuggable": variant == "usb",
            "signing": "release",
            "test_only": False,
            "certificate_sha256": certificate,
            "manifest_sha256": hashlib.sha256(
                archive.read("AndroidManifest.xml")
            ).hexdigest(),
        }
        if any(metadata.get(k) != v for k, v in expected.items()):
            raise ValueError("APK metadata does not match this release: " + variant)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard", type=Path, required=True)
    parser.add_argument("--usb", type=Path, required=True)
    parser.add_argument(
        "--signer", type=Path, required=True, help="SDK lib/apksigner.jar"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists; use a new directory.")
    certificate = (ROOT / "docs/RELEASE_CERTIFICATE.txt").read_text().strip()
    metadata = {
        kind: validate_apk(path, kind, args.signer, certificate)
        for kind, path in (("standard", args.standard), ("usb", args.usb))
    }
    files = check()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    version = VERSION["__version__"]
    with tempfile.TemporaryDirectory(
        prefix="release-", dir=args.out.parent
    ) as temporary:
        ready = Path(temporary) / "ready"
        ready.mkdir()
        for kind, path in (("standard", args.standard), ("usb", args.usb)):
            shutil.copyfile(path, ready / f"hmos-migrate-{version}-{kind}.apk")
        with zipfile.ZipFile(
            ready / f"harmony-android-migrate-{version}-source.zip",
            "x",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for file in files:
                archive.write(
                    file, "harmony-android-migrate/" + file.relative_to(ROOT).as_posix()
                )
        (ready / "BUILD-INFO.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "certificate_sha256": certificate,
                    "variants": metadata,
                    "source_files": len(files),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (ready / "SHA256SUMS.txt").write_text(
            "".join(
                hashlib.sha256(file.read_bytes()).hexdigest() + "  " + file.name + "\n"
                for file in sorted(ready.iterdir())
                if file.is_file()
            ),
            encoding="ascii",
        )
        ready.rename(args.out)
    print("Verified release bundle:", args.out.resolve())


if __name__ == "__main__":
    main()
