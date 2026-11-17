#!/usr/bin/env python3
"""Create a release identity once, outside the source tree; never print passwords."""

import argparse
import os
from pathlib import Path
import secrets
from build_android import ROOT, run, certificate_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    destination = args.out_dir.resolve()
    if destination.is_relative_to(ROOT):
        parser.error("Choose a private directory outside the source repository.")
    if destination.exists():
        parser.error("Directory already exists; never replace a release identity.")
    old_mask = os.umask(0o077)
    try:
        destination.mkdir(parents=True, mode=0o700)
        password = destination / "password.txt"
        password.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        password.chmod(0o600)
        keystore = destination / "release.jks"
        run(
            [
                "keytool",
                "-genkeypair",
                "-keystore",
                keystore,
                "-storetype",
                "PKCS12",
                "-storepass:file",
                password,
                "-keypass:file",
                password,
                "-alias",
                "hmos-release",
                "-keyalg",
                "RSA",
                "-keysize",
                "3072",
                "-validity",
                "10950",
                "-dname",
                "CN=Harmony Android Migrate",
            ]
        )
        keystore.chmod(0o600)
        fingerprint = certificate_sha256(keystore, "hmos-release", password)
        (destination / "certificate.sha256").write_text(
            fingerprint + "\n", encoding="ascii"
        )
        (destination / "BACKUP.txt").write_text(
            "Keep this entire directory in a private encrypted backup.\n"
            "release.jks and password.txt are secrets; never upload them or add them to Git.\n"
            "Reuse this identity for every release. Do not regenerate it after publishing.\n"
            "On Windows, restrict the directory ACL to your user before using it.\n",
            encoding="utf-8",
        )
    finally:
        os.umask(old_mask)
    print(
        "Release identity created outside the repository. Back up the directory privately."
    )
    print("Public certificate SHA-256:", fingerprint)


if __name__ == "__main__":
    main()
