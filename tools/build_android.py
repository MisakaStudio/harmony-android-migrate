#!/usr/bin/env python3
"""Build the local APK using an installed Android SDK; no network downloads."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ANDROID = "http://schemas.android.com/apk/res/android"
VERSION = runpy.run_path(str(ROOT / "src/hmos_migrate/__init__.py"))


def certificate_sha256(keystore, alias, password_file):
    result = subprocess.run(
        [
            "keytool",
            "-exportcert",
            "-keystore",
            str(keystore),
            "-alias",
            alias,
            "-storepass:file",
            str(password_file),
        ],
        capture_output=True,
    )
    if result.returncode:
        raise ValueError(
            "Cannot read signing certificate; check keystore, alias and password file."
        )
    return hashlib.sha256(result.stdout).hexdigest()


def verify_apk(signer, apk, expected_certificate):
    output = run(["java", "-jar", signer, "verify", "--verbose", "--print-certs", apk])
    certificates = re.findall(
        r"Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]+)", output
    )
    if certificates != [expected_certificate.lower()]:
        raise ValueError(
            "APK certificate does not match the expected release identity."
        )
    return output


def prepare_manifest(destination, variant, instrumentation=False):
    if instrumentation and variant != "usb":
        raise ValueError("Instrumentation requires --variant usb.")
    ET.register_namespace("android", ANDROID)
    tree = ET.parse(ROOT / "android/app/src/main/AndroidManifest.xml")
    manifest = tree.getroot()
    manifest.set(f"{{{ANDROID}}}versionName", VERSION["__version__"])
    manifest.set(f"{{{ANDROID}}}versionCode", str(VERSION["ANDROID_VERSION_CODE"]))
    app = manifest.find("application")
    app.set(f"{{{ANDROID}}}debuggable", str(variant == "usb").lower())
    app.set(
        f"{{{ANDROID}}}label", "鸿蒙本地迁移" + (" · USB" if variant == "usb" else "")
    )
    if instrumentation:
        app.set(f"{{{ANDROID}}}testOnly", "true")
        ET.SubElement(
            manifest,
            "instrumentation",
            {
                f"{{{ANDROID}}}name": "dev.hmos.migrate.SmokeTests",
                f"{{{ANDROID}}}targetPackage": "dev.hmos.migrate",
                f"{{{ANDROID}}}label": "Synthetic emulator tests",
            },
        )
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return manifest


def run(command):
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Build tool failed")
    return result.stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--sdk",
        type=Path,
        default=Path(os.environ["ANDROID_HOME"])
        if "ANDROID_HOME" in os.environ
        else None,
    )
    p.add_argument(
        "--tool-dir",
        type=Path,
        help="Developer-only: android.jar, aapt2, d8.jar/r8.jar, apksigner.jar",
    )
    p.add_argument("--api", default="36")
    p.add_argument("--build-tools", default="36.0.0")
    p.add_argument("--variant", choices=("standard", "usb"), default="standard")
    p.add_argument(
        "--signing", choices=("development", "release"), default="development"
    )
    p.add_argument("--keystore", type=Path)
    p.add_argument("--key-alias", default="hmos-release")
    p.add_argument("--store-pass-file", type=Path)
    p.add_argument("--key-pass-file", type=Path)
    p.add_argument("--expected-cert-sha256")
    p.add_argument("--out", type=Path)
    p.add_argument(
        "--instrumentation",
        action="store_true",
        help="Include synthetic-only emulator tests",
    )
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    if args.instrumentation and (
        args.variant != "usb" or args.signing != "development"
    ):
        p.error("Instrumentation is restricted to a USB development build.")
    if args.out is None:
        suffix = VERSION["__version__"] if args.signing == "release" else "dev"
        args.out = ROOT / f"dist/hmos-migrate-{suffix}-{args.variant}.apk"
    if args.out.exists() and not args.force:
        p.error("Output already exists; use --force to replace this build artifact.")
    if args.tool_dir:
        tools = args.tool_dir.resolve()
        android_jar = tools / "android.jar"
        d8 = tools / "d8.jar" if (tools / "d8.jar").exists() else tools / "r8.jar"
        signer = tools / "apksigner.jar"
    elif args.sdk:
        tools = args.sdk.resolve() / "build-tools" / args.build_tools
        android_jar = (
            args.sdk.resolve() / "platforms" / ("android-" + args.api) / "android.jar"
        )
        d8, signer = tools / "lib/d8.jar", tools / "lib/apksigner.jar"
    else:
        p.error(
            "Set --sdk or ANDROID_HOME. Install platforms;android-36 and build-tools;36.0.0."
        )
    aapt = tools / ("aapt2.exe" if os.name == "nt" else "aapt2")
    for tool in (android_jar, d8, signer, aapt):
        if not tool.is_file():
            p.error("Required build tool not found: " + str(tool))
    for tool in ("java", "javac", "keytool"):
        if not shutil.which(tool):
            p.error("JDK 17+ required; missing " + tool)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    if args.signing == "release":
        if (
            not args.keystore
            or not args.store_pass_file
            or not re.fullmatch(r"[a-fA-F0-9]{64}", args.expected_cert_sha256 or "")
        ):
            p.error(
                "Release signing requires --keystore, --store-pass-file and --expected-cert-sha256."
            )
        keystore = args.keystore.resolve()
        password_file = args.store_pass_file.resolve()
        if keystore.is_relative_to(ROOT) or password_file.is_relative_to(ROOT):
            p.error(
                "Release signing secrets must be stored outside the source repository."
            )
        if not keystore.is_file() or not password_file.is_file():
            p.error(
                "Release signing files do not exist; release builds never generate new keys."
            )
        certificate = certificate_sha256(keystore, args.key_alias, password_file)
        if certificate != args.expected_cert_sha256.lower():
            p.error("Signing key differs from the expected release certificate.")
        signing_args = [
            "--ks",
            keystore,
            "--ks-key-alias",
            args.key_alias,
            "--ks-pass",
            "file:" + str(password_file),
        ]
        # apksigner consumes subsequent lines if the same password file is reused.
        # With no separate key password it first tries the keystore password.
        if args.key_pass_file and args.key_pass_file.resolve() != password_file:
            key_password = args.key_pass_file.resolve()
            if key_password.is_relative_to(ROOT) or not key_password.is_file():
                p.error(
                    "The key password file must exist outside the source repository."
                )
            signing_args += ["--key-pass", "file:" + str(key_password)]
    else:
        if any(
            (
                args.keystore,
                args.store_pass_file,
                args.key_pass_file,
                args.expected_cert_sha256,
            )
        ):
            p.error("Use --signing release when providing signing credentials.")
        keystore = build / "local-debug.jks"
    if args.signing == "development" and not keystore.exists():
        run(
            [
                "keytool",
                "-genkeypair",
                "-keystore",
                keystore,
                "-storepass",
                "android",
                "-keypass",
                "android",
                "-alias",
                "local-debug",
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "3650",
                "-dname",
                "CN=HMOS Local Debug",
            ]
        )
        keystore.chmod(0o600)
    if args.signing == "development":
        with tempfile.NamedTemporaryFile(mode="w", dir=build, delete=False) as password:
            password.write("android\n")
            password_path = Path(password.name)
        try:
            certificate = certificate_sha256(keystore, "local-debug", password_path)
        finally:
            password_path.unlink()
        signing_args = [
            "--ks",
            keystore,
            "--ks-key-alias",
            "local-debug",
            "--ks-pass",
            "pass:android",
            "--key-pass",
            "pass:android",
        ]
    with tempfile.TemporaryDirectory(prefix="apk-", dir=build) as tmp:
        work = Path(tmp)
        (work / "classes").mkdir()
        (work / "dex").mkdir()
        manifest = work / "AndroidManifest.xml"
        manifest_root = prepare_manifest(manifest, args.variant, args.instrumentation)
        sources = list((ROOT / "android/app/src/main/java").rglob("*.java"))
        if args.instrumentation:
            sources += list((ROOT / "android/app/src/androidTest/java").rglob("*.java"))
        run(
            [
                aapt,
                "link",
                "-I",
                android_jar,
                "--manifest",
                manifest,
                "-o",
                work / "unsigned.apk",
            ]
        )
        run(
            [
                "javac",
                "--release",
                "8",
                "-cp",
                android_jar,
                "-d",
                work / "classes",
                *sources,
            ]
        )
        run(
            [
                "java",
                "-cp",
                d8,
                "com.android.tools.r8.D8",
                "--min-api",
                "31",
                "--lib",
                android_jar,
                "--output",
                work / "dex",
                *list((work / "classes").rglob("*.class")),
            ]
        )
        with zipfile.ZipFile(work / "unsigned.apk", "a") as archive:
            archive.write(work / "dex/classes.dex", "classes.dex")
            archive.writestr(
                "assets/hmos-migrate.json",
                json.dumps(
                    {
                        "package": manifest_root.get("package"),
                        "protocol": 1,
                        "version": VERSION["__version__"],
                        "version_code": VERSION["ANDROID_VERSION_CODE"],
                        "variant": args.variant,
                        "signing": args.signing,
                        "certificate_sha256": certificate,
                        "test_only": args.instrumentation,
                        "debuggable": ET.parse(manifest)
                        .getroot()
                        .find("application")
                        .get(f"{{{ANDROID}}}debuggable")
                        == "true",
                        "manifest_sha256": hashlib.sha256(
                            archive.read("AndroidManifest.xml")
                        ).hexdigest(),
                    }
                ),
            )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "java",
                "-jar",
                signer,
                "sign",
                *signing_args,
                "--out",
                args.out,
                work / "unsigned.apk",
            ]
        )
        verify_apk(signer, args.out, certificate)
        print("Signature verified:", certificate)
    print("Built:", args.out.resolve())


if __name__ == "__main__":
    main()
