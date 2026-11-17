"""All records and cryptographic keys in this module are synthetic test data."""

import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from hmos_migrate.backup import DB_PATHS, MODULES

PASSWORD = b"synthetic-test-password"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII="
)
MAP = {
    "schema": "hmos-migrate/sim-map",
    "version": 1,
    "slots": {
        "0": {
            "sub_id": 101,
            "phone_account": {
                "id": "account-alpha",
                "component": "com.example.phone/.ConnectionService",
                "user_id": 0,
            },
            "call_extras": {"simid": 909},
        },
        "1": {
            "sub_id": 203,
            "phone_account": {
                "id": "account-beta",
                "component": "com.example.phone/.ConnectionService",
                "user_id": 0,
            },
        },
    },
}


def tar_bytes(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return out.getvalue()


def make_backup(
    root,
    *,
    sms=True,
    calls=True,
    mms=False,
    missing_mms=False,
    duplicates=False,
    split=False,
    rcs=False,
    zero_date=False,
    empty=False,
):
    root = Path(root)
    source = root / "encrypted"
    source.mkdir()
    raw = root / "db-source"
    raw.mkdir()
    connections = []
    members = {k: {} for k in MODULES}

    def db(name, sql):
        path = raw / (name + ".db")
        c = sqlite3.connect(path)
        connections.append((name, path, c))
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA wal_autocheckpoint=0")
        c.executescript(sql)
        return c

    if sms or mms:
        compat = db(
            "sms",
            "CREATE TABLE sms_tb(address TEXT,date INTEGER,body TEXT,sub_id INTEGER,type INTEGER,read INTEGER,date_sent INTEGER,status INTEGER,protocol INTEGER,subject TEXT,locked INTEGER,service_center TEXT);",
        )
        native = db(
            "sms_native",
            """CREATE TABLE sms_mms_info(msg_id INTEGER PRIMARY KEY,sender_number TEXT,receiver_number TEXT,is_sender INTEGER,msg_type INTEGER,start_time TEXT,msg_content TEXT,slot_id INTEGER,msg_state INTEGER,is_read INTEGER,is_lock INTEGER,msg_title TEXT);
            CREATE TABLE rcs_info(rcs_id INTEGER PRIMARY KEY);
            CREATE TABLE mms_part(msg_id INTEGER,type INTEGER,ct TEXT,location_path TEXT,content TEXT);""",
        )
        if sms:
            for i in range(2 if duplicates else 1):
                compat.execute(
                    "INSERT INTO sms_tb VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "+15550001001",
                        1700000000123,
                        "合成消息 <&>\n🙂",
                        0,
                        1,
                        1,
                        1700000000000,
                        -1,
                        0,
                        None,
                        0,
                        None,
                    ),
                )
                native.execute(
                    "INSERT INTO sms_mms_info VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        i + 1,
                        "+15550001001",
                        "",
                        1,
                        0,
                        "1700000000123",
                        "合成消息 <&>\n🙂",
                        0,
                        0,
                        1,
                        0,
                        "",
                    ),
                )
        if rcs:
            native.execute("INSERT INTO rcs_info VALUES(1)")
        if mms:
            native.execute(
                "INSERT INTO sms_mms_info VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (30, "+15550001002", "", 1, 1, "1700000500456", "", 1, 0, 1, 0, ""),
            )
            path = "/data/storage/el2/base/haps/entry/files/1700000500000/image.png"
            native.execute(
                "INSERT INTO mms_part VALUES(30,1,'image/png',?,'')", (path,)
            )
            if not missing_mms:
                members["mms"][path] = PNG
    if calls:
        compat = db(
            "calls",
            "CREATE TABLE calls_tb(number TEXT,duration INTEGER,date INTEGER,type INTEGER,presentation INTEGER,subscription_id TEXT);",
        )
        native = db(
            "calls_native",
            "CREATE TABLE calllog(id INTEGER PRIMARY KEY,phone_number TEXT,talk_duration INTEGER,begin_time INTEGER,end_time INTEGER,create_time INTEGER,call_direction INTEGER,answer_state INTEGER,slot_id INTEGER);",
        )
        begin = 0 if zero_date else 1700000100
        compat.execute(
            "INSERT INTO calls_tb VALUES(?,?,?,?,?,?)",
            ("+15550002001", 0 if zero_date else 12, begin * 1000, 2, 1, "0"),
        )
        native.execute(
            "INSERT INTO calllog VALUES(1,?,?,?,?,?,?,?,?)",
            (
                "+15550002001",
                0 if zero_date else 12,
                begin,
                0 if zero_date else begin + 12,
                1700000099,
                1,
                0,
                0,
            ),
        )
    for name, path, c in connections:
        if empty:
            for (table,) in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall():
                c.execute('DELETE FROM "' + table.replace('"', '""') + '"')
        c.commit()
        kind, member = DB_PATHS[name]
        members[kind][member] = path.read_bytes()
        wal = Path(str(path) + "-wal")
        if wal.exists():
            members[kind][member + "-wal"] = wal.read_bytes()
    for _, _, c in connections:
        c.close()
    encode = lambda value: base64.b64encode(value).decode()
    salt, iv, master = b"S" * 32, b"I" * 16, bytes(range(24))
    key = hashlib.pbkdf2_hmac("sha256", PASSWORD, salt, 10000, 32)
    cfg = {
        "encryptionType": 2,
        "backupSalt": encode(salt),
        "backupIv": encode(iv),
        "backupKey": encode(
            bytes.fromhex("0000000c0000001000000000")
            + iv
            + AESGCM(key).encrypt(iv, master, None)
        ),
    }
    (source / ".info.json").write_text(
        json.dumps(
            {
                "BackupFileTypeInfo": cfg,
                "BackupFileVersionInfo": {"versionName": "synthetic"},
            }
        )
    )
    for kind, files in members.items():
        if not files:
            continue
        module = MODULES[kind]
        folder = source / "files/BASIC" / module
        folder.mkdir(parents=True)
        msalt = hashlib.sha256(module.encode()).digest()
        miv = b"V" * 16
        aes = AESGCM(hashlib.pbkdf2_hmac("sha256", master, msalt, 10000, 32))
        groups = [files]
        if split:
            groups = [
                {k: v for k, v in files.items() if not k.endswith("-wal")},
                {k: v for k, v in files.items() if k.endswith("-wal")},
            ]
        manifest = []
        for i, group in enumerate(groups):
            data = tar_bytes(group)
            name = f"part.{i}.tar"
            (folder / name).write_bytes(aes.encrypt(miv, data, None))
            manifest.append(
                {
                    "fileName": name,
                    "information": {
                        "path": "/backup/" + name,
                        "stat": {"st_size": len(data)},
                    },
                    "isBigFile": False,
                }
            )
        (folder / "manage.json").write_bytes(
            aes.encrypt(miv, json.dumps(manifest).encode(), None)
        )
        (folder / ".info.json").write_text(
            json.dumps(
                {
                    "BackupFileModuleInfo": [
                        {
                            "moduleName": module,
                            "encryptionSalt": encode(msalt),
                            "encryptionIv": encode(miv),
                        }
                    ]
                }
            )
        )
    return source
