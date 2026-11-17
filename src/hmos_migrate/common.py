import hashlib
import json
import os
from pathlib import Path


class MigrationError(Exception):
    """A safe, actionable error: never include message bodies, credentials or raw dumps."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def fail(code, message):
    raise MigrationError(code, message)


def read_json(path, limit=32 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        fail("file_too_large", f"JSON 超过 {limit // 1024 // 1024} MiB 限制。")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        fail("invalid_json", "文件不是有效的 UTF-8 JSON。")


def write_json(path, data):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    path.chmod(0o600)


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def private_umask():
    os.umask(0o077)


def safe_text(value, default=""):
    value = default if value is None else str(value)
    if any(
        not (
            c in "\t\n\r"
            or 0x20 <= ord(c) <= 0xD7FF
            or 0xE000 <= ord(c) <= 0xFFFD
            or 0x10000 <= ord(c) <= 0x10FFFF
        )
        for c in value
    ):
        fail("invalid_xml_character", "正文含 XML 1.0 不支持的字符；未静默删除字符。")
    return value
