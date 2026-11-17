#!/usr/bin/env python3
"""Check the publishable source tree without opening ignored private/build files."""

from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("src", "android", "tests", "examples", "docs", "tools", ".github")
TOP_FILES = (
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "MANIFEST.in",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "PRIVACY.md",
)
ALLOWED_SUFFIXES = {
    ".py",
    ".java",
    ".xml",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".in",
    ".txt",
}


def source_files():
    files = [ROOT / name for name in TOP_FILES if (ROOT / name).is_file()]
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*"):
            if any(
                part in {"__pycache__", ".ruff_cache", "build", "dist", "private"}
                or part.startswith(".hmos-work-")
                or part.endswith(".egg-info")
                for part in path.parts
            ):
                continue
            if path.is_symlink():
                raise ValueError(
                    "Source symlink is not allowed: " + str(path.relative_to(ROOT))
                )
            if path.is_file():
                if path.parent == ROOT / "docs/images" and path.name in {
                    "home.png",
                    "mapping.png",
                }:
                    files.append(path)
                    continue
                if path.suffix not in ALLOWED_SUFFIXES:
                    raise ValueError(
                        "Unexpected source artifact: " + str(path.relative_to(ROOT))
                    )
                files.append(path)
    return sorted(set(files))


def check(git_index=False):
    files = source_files()
    for path in files:
        if path.suffix == ".png":
            if not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid documentation screenshot")
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"/Users/[A-Za-z0-9_.-]+/", text) or re.search(
            r"[A-Z]:\\Users\\[A-Za-z0-9_.-]+\\", text
        ):
            raise ValueError(
                "Private workstation path in " + str(path.relative_to(ROOT))
            )
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", text):
            raise ValueError("Private key material in " + str(path.relative_to(ROOT)))
        if path.suffix == ".json":
            if not path.name.endswith((".example.json", ".schema.json")):
                raise ValueError(
                    "Operational JSON is not a source artifact: "
                    + str(path.relative_to(ROOT))
                )
            data = json.loads(text)
            if isinstance(data, dict) and any(
                key in data for key in ("records", "sms", "calls", "BackupFileTypeInfo")
            ):
                raise ValueError("Communication/backup data found in source JSON")
        if path.suffix == ".xml" and path.parent == ROOT / "examples":
            for node in ET.fromstring(text).iter():
                for key in ("address", "number"):
                    if key in node.attrib and not node.attrib[key].startswith("+1555"):
                        raise ValueError(
                            "Example number is not an explicit synthetic number"
                        )
    manifest = ET.parse(ROOT / "android/app/src/main/AndroidManifest.xml").getroot()
    ns = "{http://schemas.android.com/apk/res/android}"
    permissions = {p.get(ns + "name") for p in manifest.findall("uses-permission")}
    if "android.permission.INTERNET" in permissions:
        raise ValueError("The local importer must not request INTERNET")
    if manifest.find("instrumentation") is not None:
        raise ValueError("Test instrumentation must not be in the normal manifest")
    if manifest.find("application").get(ns + "debuggable") != "false":
        raise ValueError("The base manifest must default to non-debuggable")
    if git_index:
        staged = (
            subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
            .decode()
            .split("\0")
        )
        allowed = {p.relative_to(ROOT).as_posix() for p in files}
        if set(filter(None, staged)) != allowed:
            raise ValueError("Git index differs from the exact publishable source list")
        for name in filter(None, staged):
            if (
                subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
                != (ROOT / name).read_bytes()
            ):
                raise ValueError(
                    "Git index content differs from checked source: " + name
                )
    if not (ROOT / ".gitignore").read_text().find("*.jks") >= 0:
        raise ValueError("Signing keys are not ignored")
    print(
        f"Source checks passed: {len(files)} files; no prohibited artifacts, key blocks, or workstation paths detected."
    )
    return files


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--git-index", action="store_true")
        check(parser.parse_args().git_index)
    except (ValueError, OSError, ET.ParseError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
