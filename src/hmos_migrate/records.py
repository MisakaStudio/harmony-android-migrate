import base64
from collections import Counter, defaultdict, deque
import hashlib
from pathlib import Path
from .backup import Archives, extract_databases, table_rows
from .common import digest, fail, safe_text


def call_type(row):
    if row["call_direction"] == 1:
        return 2
    if row["call_direction"] != 0 or row["answer_state"] not in (0, 1, 2, 4, 6):
        fail("unsupported_call_type", "发现未知通话类型，不猜测映射。")
    return {0: 3, 1: 1, 2: 5, 4: 1, 6: 6}[row["answer_state"]]


def load_records(decrypted, work, categories):
    categories = set(categories)
    paths = extract_databases(decrypted, Path(work) / "databases", categories)
    result = {"sms": [], "calls": [], "mms": []}
    issues, repairs = [], []
    occurrences = Counter()

    def record(kind, values, slot, **extra):
        item = {"source_slot": int(slot), "values": values, **extra}
        fingerprint = digest({"kind": kind, **item})
        occurrence = occurrences[fingerprint]
        occurrences[fingerprint] += 1
        item["source_id"] = digest([fingerprint, occurrence])
        result[kind].append(item)
        return item

    def issue(kind, source_id, reason):
        issues.append({"category": kind, "source_id": str(source_id), "reason": reason})

    native_messages = []
    if categories & {"sms", "mms"}:
        native_messages = table_rows(paths["sms_native"], "sms_mms_info")
        rcs = table_rows(paths["sms_native"], "rcs_info", optional=True)
        for row in rcs:
            issue("rcs", row.get("rcs_id", row["source_rowid"]), "unsupported_rcs")
        for row in native_messages:
            if row["msg_type"] not in (0, 1):
                issue(
                    "messages",
                    row.get("msg_id", row["source_rowid"]),
                    "unsupported_message_type",
                )
    if "sms" in categories:
        sms = table_rows(paths["sms"], "sms_tb")
        original = [r for r in native_messages if r["msg_type"] == 0]

        def native_key(r):
            if r["is_sender"] not in (0, 1):
                fail("unsupported_sms_direction", "未知的短信方向。")
            return (
                r["sender_number"] if r["is_sender"] else r["receiver_number"],
                int(r["start_time"]),
                r["msg_content"],
                r["slot_id"],
            )

        def compat_key(r):
            return (r["address"], r["date"], r["body"], r["sub_id"])

        if Counter(native_key(r) for r in original) != Counter(
            compat_key(r) for r in sms
        ):
            fail(
                "sms_crosscheck_failed",
                "兼容短信与原始库的号码、正文、时间或卡槽不一致，停止转换。",
            )
        originals = defaultdict(deque)
        for row in original:
            originals[native_key(row)].append(row)
        for row in sms:
            native = originals[compat_key(row)].popleft()
            if row["type"] not in (1, 2):
                issue("sms", native["msg_id"], "unsupported_draft_failed_or_queued_sms")
                continue
            if row["type"] != (1 if native["is_sender"] else 2):
                fail("sms_direction_mismatch", "短信收发方向交叉检查失败。")
            if (
                row["date"] <= 0
                or row["read"] not in (0, 1)
                or row["body"] is None
                or not row["address"]
            ):
                fail("invalid_sms", "短信缺少有效号码、正文、日期或读取状态。")
            values = {
                "protocol": int(row.get("protocol") or 0),
                "address": safe_text(row["address"]),
                "date": int(row["date"]),
                "date_sent": int(row.get("date_sent") or 0),
                "type": row["type"],
                "subject": row.get("subject"),
                "body": safe_text(row["body"]),
                "service_center": row.get("service_center"),
                "read": row["read"],
                "status": int(row["status"] if row.get("status") is not None else -1),
                "locked": int(row.get("locked") or 0),
                "sub_id": -1,
            }
            record("sms", values, row["sub_id"])
    if "calls" in categories:
        calls = table_rows(paths["calls"], "calls_tb")
        native_calls = table_rows(paths["calls_native"], "calllog")

        def native_key(r):
            return (
                r["phone_number"],
                r["talk_duration"],
                (r["begin_time"] or r["end_time"]) * 1000,
                call_type(r),
                str(r["slot_id"]),
            )

        def compat_key(r):
            return (
                r["number"],
                r["duration"],
                r["date"],
                r["type"],
                str(r["subscription_id"]),
            )

        if Counter(native_key(r) for r in native_calls) != Counter(
            compat_key(r) for r in calls
        ):
            fail("calls_crosscheck_failed", "兼容通话与原始库不一致，停止转换。")
        originals = defaultdict(deque)
        for row in native_calls:
            originals[native_key(row)].append(row)
        for row in calls:
            native = originals[compat_key(row)].popleft()
            date = row["date"] or int(native.get("create_time") or 0) * 1000
            if date <= 0 or row["duration"] < 0:
                fail("invalid_call", "通话日期或时长无效。")
            item = record(
                "calls",
                {
                    "number": safe_text(row["number"]),
                    "date": date,
                    "duration": row["duration"],
                    "type": row["type"],
                    "presentation": row.get("presentation")
                    or (1 if row["number"] else 3),
                },
                native["slot_id"],
            )
            if not row["date"]:
                repairs.append(
                    {
                        "source_id": item["source_id"],
                        "field": "date",
                        "reason": "used_native_creation_time",
                    }
                )
    if "mms" in categories:
        parts = table_rows(paths["sms_native"], "mms_part", optional=True)
        with Archives(decrypted, "mms") as archive:
            for row in native_messages:
                if row["msg_type"] != 1:
                    continue
                media = [p for p in parts if p["msg_id"] == row["msg_id"]]
                images = [p for p in media if p["type"] == 1]
                if not images:
                    issue("mms", row["msg_id"], "no_supported_attachment")
                    continue
                if row["is_sender"] != 1 or row["msg_state"] != 0:
                    issue("mms", row["msg_id"], "unsupported_mms_direction_or_state")
                    continue
                if row["msg_content"] or any(
                    p["type"] not in (0, 1) or p.get("content") for p in media
                ):
                    issue("mms", row["msg_id"], "unsupported_mms_text_or_parts")
                    continue
                imported_parts, missing = [], False
                for i, part in enumerate(images):
                    if part["ct"] not in ("image/jpeg", "image/png"):
                        issue("mms", row["msg_id"], "unsupported_attachment_type")
                        missing = True
                        break
                    data = archive.read(part["location_path"], 20 * 1024 * 1024)
                    if data is None:
                        issue("mms", row["msg_id"], "missing_attachment_file")
                        missing = True
                        break
                    name = f"image_{i}." + (
                        "jpg" if part["ct"] == "image/jpeg" else "png"
                    )
                    imported_parts.append(
                        {
                            "values": {
                                "seq": i,
                                "ct": part["ct"],
                                "cl": name,
                                "cid": f"<{name}>",
                                "name": name,
                            },
                            "data": base64.b64encode(data).decode(),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
                if missing:
                    continue
                date = int(row["start_time"])
                if date <= 0 or not row["sender_number"]:
                    fail("invalid_mms", "彩信缺少有效日期或发送方。")
                addresses = [
                    {
                        "address": safe_text(row["sender_number"]),
                        "type": 137,
                        "charset": 106,
                    }
                ]
                if row.get("receiver_number"):
                    addresses.append(
                        {
                            "address": safe_text(row["receiver_number"]),
                            "type": 151,
                            "charset": 106,
                        }
                    )
                values = {
                    "date": date // 1000,
                    "date_sent": 0,
                    "msg_box": 1,
                    "m_type": 132,
                    "read": int(row["is_read"]),
                    "locked": int(row["is_lock"]),
                    "sub_id": -1,
                    "ct_t": "application/vnd.wap.multipart.related",
                    "text_only": 0,
                    "sub": safe_text(row.get("msg_title")),
                    "sub_cs": 106,
                    "m_size": sum(
                        len(base64.b64decode(p["data"])) for p in imported_parts
                    ),
                }
                item = record(
                    "mms",
                    values,
                    row["slot_id"],
                    parts=imported_parts,
                    addresses=addresses,
                )
                values["m_id"] = values["tr_id"] = "hmos-" + item["source_id"][:32]
    report = {
        "categories": sorted(categories),
        "counts": {k: len(v) for k, v in result.items()},
        "source_slots": sorted(
            {str(r["source_slot"]) for rows in result.values() for r in rows}
        ),
        "issues": issues,
        "repairs": repairs,
        "complete": not issues,
    }
    return result, report
