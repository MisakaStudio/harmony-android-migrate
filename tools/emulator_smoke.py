#!/usr/bin/env python3
"""Run synthetic Provider tests on an explicitly named, isolated Android emulator."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import re
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from hmos_migrate.cli import convert
from synthetic import make_backup, PASSWORD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--server-port", default="5037")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--expected-avd", required=True)
    parser.add_argument("--apk", type=Path, required=True)
    args = parser.parse_args()
    if not args.serial.startswith("emulator-"):
        parser.error("Refusing a non-emulator serial.")
    command = [args.adb, "-P", args.server_port, "-s", args.serial]

    def adb(*parts, data=None, check=True, timeout=30):
        result = subprocess.run(
            command + list(parts), input=data, capture_output=True, timeout=timeout
        )
        if check and result.returncode:
            raise RuntimeError("ADB test command failed: " + " ".join(parts[:3]))
        return result.stdout.decode(errors="replace")

    if adb("shell", "getprop", "ro.hardware").strip() != "ranchu":
        parser.error("Refusing a device without emulator hardware.")
    name = (
        adb("shell", "getprop", "ro.boot.qemu.avd_name").strip()
        or adb("shell", "getprop", "ro.kernel.qemu.avd_name").strip()
    )
    if name != args.expected_avd:
        parser.error(
            "AVD name does not match the explicitly selected isolated test device."
        )
    if adb("shell", "getprop", "sys.boot_completed").strip() != "1":
        parser.error("Emulator is not fully booted.")
    if adb("shell", "am", "get-current-user").strip() != "0":
        parser.error("Only primary-user test emulators are supported.")
    original = adb(
        "shell",
        "cmd",
        "role",
        "get-role-holders",
        "--user",
        "0",
        "android.app.role.SMS",
        check=False,
    ).strip()
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", original):
        dump = adb("shell", "dumpsys", "role")
        match = re.search(
            r"name=android\.app\.role\.SMS\s+holders=([A-Za-z0-9_.]+)", dump
        )
        if not match:
            parser.error("Cannot record the emulator's original SMS role.")
        original = match[1]
    adb("install", "-r", "-t", "-g", str(args.apk.resolve()), timeout=120)
    adb("shell", "cmd", "connectivity", "airplane-mode", "enable")
    adb("shell", "svc", "wifi", "disable")
    adb(
        "shell",
        "cmd",
        "role",
        "add-role-holder",
        "--user",
        "0",
        "android.app.role.SMS",
        "dev.hmos.migrate",
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="synthetic-", dir=ROOT / "build"
        ) as temporary:
            root = Path(temporary)
            backup = make_backup(root, mms=True, duplicates=True, split=True)
            convert(backup, root / "converted", password=PASSWORD)
            document = json.loads((root / "converted/migration.json").read_text())
            nonce = uuid.uuid4().hex
            delta = int(time.time() * 1000) - 7 * 86400000 - 1700000000123
            for kind, records in document["records"].items():
                for record in records:
                    record["source_id"] = hashlib.sha256(
                        (record["source_id"] + nonce).encode()
                    ).hexdigest()
                    record["values"]["date"] += (
                        delta // 1000 if kind == "mms" else delta
                    )
                    if kind == "mms":
                        record["values"]["m_id"] = record["values"]["tr_id"] = (
                            "hmos-" + record["source_id"][:32]
                        )
            adb("shell", "run-as dev.hmos.migrate mkdir -p files")
            adb(
                "shell",
                "-T",
                "run-as dev.hmos.migrate sh -c 'cat > files/smoke-input.json'",
                data=json.dumps(document, ensure_ascii=False).encode(),
            )
            output = adb(
                "shell",
                "am",
                "instrument",
                "-w",
                "-e",
                "mode",
                "synthetic-only",
                "dev.hmos.migrate/.SmokeTests",
                timeout=180,
            )
            print(output)
            if "INSTRUMENTATION_RESULT: ok=true" not in output:
                raise RuntimeError("Android integration tests did not pass.")
            report = json.loads(
                adb(
                    "exec-out",
                    "run-as",
                    "dev.hmos.migrate",
                    "cat",
                    "files/smoke-result.json",
                )
            )
            target = ROOT / "build" / ("smoke-api-" + str(report["sdk"]) + ".json")
            target.write_text(json.dumps(report, indent=2))
            print("Synthetic integration report:", target)
    finally:
        if original:
            adb(
                "shell",
                "cmd",
                "role",
                "add-role-holder",
                "--user",
                "0",
                "android.app.role.SMS",
                original,
            )


if __name__ == "__main__":
    main()
