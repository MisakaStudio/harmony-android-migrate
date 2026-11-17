import base64
import hashlib
from pathlib import Path, PurePosixPath
import sqlite3
import tarfile
from contextlib import closing
from .common import fail, read_json

MODULES = {
    "sms": "com.ohos.telephonydataability",
    "calls": "com.ohos.contactsdataability",
    "mms": "com.ohos.mms",
}
DB_PATHS = {
    "sms": ("sms", "/data/storage/el1/database/next2hmos/sms.db"),
    "sms_native": ("sms", "/data/storage/el1/database/rdb/sms_mms.db"),
    "calls": ("calls", "/data/storage/el2/database/contactsdata/next2hmos/callslog.db"),
    "calls_native": ("calls", "/data/storage/el1/database/rdb/calls.db"),
}


def metadata_path(root):
    for name in (".info.json", "info.json"):
        path = Path(root) / name
        if path.is_file() and not path.is_symlink():
            return path
    fail("missing_metadata", "没有找到 .info.json/info.json；请选择备份时间目录。")


def inspect_backup(root):
    root = Path(root)
    info = read_json(metadata_path(root), 2 * 1024 * 1024)
    cfg = info.get("BackupFileTypeInfo", {})
    present = {
        kind: (root / "files" / "BASIC" / name).is_dir()
        for kind, name in MODULES.items()
    }
    return {
        "format": "hmos-json-v1",
        "encryption_type": cfg.get("encryptionType"),
        "backup_app_version": info.get("BackupFileVersionInfo", {}).get("versionName"),
        "modules_present": present,
        "supported_encryption": cfg.get("encryptionType") == 2,
    }


def _decode(value):
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        fail("invalid_crypto_metadata", "备份加密参数不是有效的 Base64。")


def decrypt_backup(source, output, password, categories):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    source, output = Path(source), Path(output)
    info = read_json(metadata_path(source), 2 * 1024 * 1024)
    try:
        cfg = info["BackupFileTypeInfo"]
        blob, nonce, salt = (
            _decode(cfg[k]) for k in ("backupKey", "backupIv", "backupSalt")
        )
    except KeyError:
        fail("invalid_crypto_metadata", "缺少必要加密参数。")
    if (
        cfg.get("encryptionType") != 2
        or len(blob) < 45
        or blob[:12] != bytes.fromhex("0000000c0000001000000000")
        or blob[12:28] != nonce
        or len(salt) != 32
    ):
        fail("unsupported_encryption", "尚未适配此加密格式，未尝试跳过认证。")

    def kdf(secret, salt):
        return hashlib.pbkdf2_hmac("sha256", secret, salt, 10000, 32)

    try:
        master = AESGCM(kdf(password, salt)).decrypt(nonce, blob[28:], None)
    except InvalidTag:
        fail("authentication_failed", "密码不匹配、备份损坏或加密参数不受支持。")
    needed = {"sms" if c == "mms" else c for c in categories}
    if "mms" in categories and (source / "files/BASIC" / MODULES["mms"]).is_dir():
        needed.add("mms")
    verified = 0
    for kind in sorted(needed):
        module = MODULES[kind]
        folder = source / "files/BASIC" / module
        if not folder.is_dir() or folder.is_symlink():
            fail("missing_selected_module", f"备份未包含所选的 {kind} 模块。")
        meta = read_json(metadata_path(folder), 2 * 1024 * 1024).get(
            "BackupFileModuleInfo", []
        )
        entries = [m for m in meta if m.get("moduleName") == module]
        if len(entries) != 1:
            fail("unsupported_module_metadata", f"{kind} 模块元数据不匹配。")
        entry = entries[0]
        aes = AESGCM(kdf(master, _decode(entry["encryptionSalt"])))
        iv = _decode(entry["encryptionIv"])
        target = output / module
        target.mkdir(parents=True)

        def decrypt_file(name):
            file = folder / name
            if not file.exists():
                fail(
                    "manifest_file_missing",
                    f"{kind} 清单声明的文件或分卷缺失，备份不完整。",
                )
            if file.is_symlink() or not file.is_file():
                fail("unsafe_backup_file", "备份包含非普通文件。")
            if file.stat().st_size > 512 * 1024 * 1024:
                fail(
                    "backup_volume_too_large",
                    "单个备份分卷超过 512 MiB，当前版本不支持。",
                )
            try:
                data = aes.decrypt(iv, file.read_bytes(), None)
            except InvalidTag:
                fail("authentication_failed", f"{kind} 模块文件完整性校验失败。")
            with (target / file.name).open("xb") as stream:
                stream.write(data)

        decrypt_file("manage.json")
        verified += 1
        manifest = read_json(target / "manage.json")
        if not isinstance(manifest, list):
            fail("invalid_manifest", "模块清单不是列表。")
        seen = set()
        for item in manifest:
            try:
                name, size = item["fileName"], item["information"]["stat"]["st_size"]
            except (KeyError, TypeError):
                fail("invalid_manifest", "模块清单缺少文件名或长度。")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or name in (".", "..", "manage.json", "info.json", ".info.json")
                or name in seen
            ):
                fail("unsafe_manifest", "模块清单存在不安全或重复的文件名。")
            seen.add(name)
            decrypt_file(name)
            verified += 1
            if type(size) is not int or size != (target / name).stat().st_size:
                fail("manifest_size_mismatch", f"{kind} 解密分卷长度与清单不符。")
    return verified


class Archives:
    def __init__(self, decrypted, kind):
        self.files = []
        self.members = {}
        self.external = {}
        folder = Path(decrypted) / MODULES[kind]
        if (folder / "manage.json").is_file():
            for item in read_json(folder / "manage.json"):
                name = item["fileName"]
                if Path(name).name != name or name in (".", ".."):
                    fail("unsafe_manifest", "模块清单包含不安全路径。")
                file = folder / name
                if item.get("isBigFile") and file.is_file():
                    self.external[item["information"]["path"]] = file
        for path in sorted((Path(decrypted) / MODULES[kind]).glob("part.*.tar")):
            try:
                archive = tarfile.open(path)
                self.files.append(archive)
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    name = "/" + member.name.lstrip("/")
                    if ".." in PurePosixPath(name).parts:
                        fail("unsafe_archive", "归档包含上级目录路径。")
                    if name in self.members:
                        fail("duplicate_archive_member", "归档分卷中出现重复文件。")
                    self.members[name] = (archive, member)
            except tarfile.TarError:
                self.close()
                fail("invalid_archive", f"{kind} 分卷不是有效 tar。")

    def read(self, name, limit=128 * 1024 * 1024):
        if name in self.external:
            file = self.external[name]
            if file.stat().st_size > limit:
                fail("archive_member_too_large", "附件超出当前处理限制。")
            return file.read_bytes()
        pair = self.members.get("/" + name.lstrip("/"))
        if pair is None:
            return None
        archive, member = pair
        if member.size > limit:
            fail("archive_member_too_large", "归档文件超出当前处理限制。")
        return archive.extractfile(member).read()

    def close(self):
        for archive in self.files:
            archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def extract_databases(decrypted, destination, categories):
    result = {}
    wanted = ({"sms", "sms_native"} if set(categories) & {"sms", "mms"} else set()) | (
        {"calls", "calls_native"} if "calls" in categories else set()
    )
    for kind in ("sms", "calls"):
        if not any(DB_PATHS[name][0] == kind for name in wanted):
            continue
        with Archives(decrypted, kind) as archives:
            for name in sorted(wanted):
                module, member = DB_PATHS[name]
                if module != kind:
                    continue
                folder = Path(destination) / name
                folder.mkdir(parents=True)
                data = archives.read(member)
                if data is None:
                    fail(
                        "unsupported_database_layout",
                        f"{name} 数据库缺失；不把格式不兼容当作空数据。",
                    )
                target = folder / Path(member).name
                target.write_bytes(data)
                wal = archives.read(member + "-wal")
                if wal is not None:
                    Path(str(target) + "-wal").write_bytes(wal)
                result[name] = target
    return result


def table_rows(path, table, *, optional=False):
    try:
        with closing(
            sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                fail("database_corrupt", "数据库完整性检查失败。")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                if optional:
                    return []
                fail("unsupported_database_schema", f"缺少必要表 {table}。")
            return [
                dict(r)
                for r in db.execute(
                    'SELECT rowid AS source_rowid,* FROM "' + table + '" ORDER BY rowid'
                )
            ]
    except sqlite3.DatabaseError:
        fail("database_unreadable", f"无法读取 {table}；可能是未支持的数据库版本。")
