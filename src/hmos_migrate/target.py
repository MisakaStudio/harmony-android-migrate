"""Identify documented target formats without copying target communication records."""

from collections import Counter, defaultdict
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
from .common import fail
from .sim import PROFILE_SCHEMA, component_name

MAX_SAMPLE = 64 * 1024 * 1024
REQUIRED_FIELDS = {
    "sms": {"address", "date", "type", "read", "body"},
    "calls": {"number", "duration", "date", "type", "presentation"},
    "mms": {"date", "msg_box"},
}


def _payload(path):
    path = Path(path)
    if path.stat().st_size > MAX_SAMPLE:
        fail(
            "target_sample_too_large",
            "目标样本超过 64 MiB；请导出更少记录作为格式样本。",
        )
    data = path.read_bytes()
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                files = [i for i in archive.infolist() if not i.is_dir()]
                if not files or len(files) > 65536:
                    fail(
                        "unsupported_target_archive",
                        "目标 ZIP 不是受支持的 XML 备份包。",
                    )
                if any(i.flag_bits & 1 for i in files):
                    fail(
                        "encrypted_target_sample",
                        "目标样本已加密，请先从原备份应用导出未加密 XML。",
                    )
                first = files[0]
                if (
                    first.file_size > MAX_SAMPLE
                    or sum(i.file_size for i in files) > 2 * MAX_SAMPLE
                ):
                    fail("target_sample_too_large", "目标 ZIP 解压后超出限制。")
                data = archive.read(first)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError):
            fail(
                "unsupported_target_archive", "目标 ZIP 损坏、加密或压缩方式不受支持。"
            )
    return data


def _present(value):
    return value is not None and str(value).strip() not in (
        "",
        "null",
        "NULL",
        "None",
        "-1",
    )


def _identifier(value, code):
    if not _present(value):
        return None
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        result = int(value.strip())
    else:
        fail(code, "目标卡标识必须是整数或整数字符串，不能转换小数或布尔值。")
    if result > 2147483647:
        fail(code, "目标卡标识超出 Android 整数范围。")
    return result if result >= 0 else None


def _json_rows(document):
    schema = document.get("schema")
    if type(document.get("version")) is not int or document["version"] != 1:
        fail("unknown_target_format", "目标 JSON 的版本不受支持。")
    native = schema == "hmos-migrate/native"
    if native:
        records = document.get("records")
        if not isinstance(records, dict) or set(records) != set(REQUIRED_FIELDS):
            fail(
                "invalid_target_json",
                "原生 JSON 必须明确包含 sms、calls、mms 三个类别。",
            )
        if document.get("date_units") != {
            "sms": "milliseconds",
            "calls": "milliseconds",
            "mms": "seconds",
        }:
            fail("invalid_target_json", "原生 JSON 的时间单位不受支持。")
    elif schema == "hmos-migrate/provider-backup":
        allowed = set(REQUIRED_FIELDS) | {
            "schema",
            "version",
            "captured_at",
            "mms_parts",
            "mms_addresses",
        }
        if set(document) - allowed or not set(document) & set(REQUIRED_FIELDS):
            fail("invalid_target_json", "Provider 备份缺少已知类别，或包含未知结构。")
        for kind in ("mms_parts", "mms_addresses"):
            if kind in document and (
                not isinstance(document[kind], list)
                or any(not isinstance(row, dict) for row in document[kind])
            ):
                fail("invalid_target_json", "Provider 彩信附属表格式错误。")
        records = {k: document[k] for k in REQUIRED_FIELDS if k in document}
    else:
        fail(
            "unknown_target_format",
            "JSON 没有受支持的备份 schema/version，不能按字段名称猜测。",
        )
    for kind, rows in records.items():
        if not isinstance(rows, list):
            fail("invalid_target_json", "目标 JSON 的记录类别必须是数组。")
        for record in rows:
            if not isinstance(record, dict):
                fail("invalid_target_json", "目标 JSON 的记录必须是对象。")
            row = record.get("values") if native else record
            if not isinstance(row, dict) or not REQUIRED_FIELDS[kind] <= row.keys():
                fail("incomplete_target_record", "目标 JSON 记录缺少该类别的关键字段。")
            if native and (
                not isinstance(record.get("source_id"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["source_id"])
                or type(record.get("source_slot")) is not int
                or record["source_slot"] < -1
            ):
                fail("invalid_target_json", "原生 JSON 的来源记录标识或槽位错误。")
            yield kind, row


def analyze_samples(paths):
    if not paths:
        fail("missing_target_sample", "至少提供一个目标短信或通话备份样本。")
    formats, categories, subscriptions = set(), Counter(), Counter()
    accounts, extra_values, observations = Counter(), defaultdict(set), []

    def consume(kind, row):
        categories[kind] += 1
        if kind in ("sms", "mms"):
            value = _identifier(row.get("sub_id"), "invalid_target_subscription")
            if value is not None:
                subscriptions[value] += 1
        elif kind == "calls":
            account, component = (
                row.get("subscription_id"),
                row.get("subscription_component_name"),
            )
            if _present(account) and _present(component):
                if not isinstance(account, str) or not isinstance(component, str):
                    fail("invalid_target_account", "目标通话账户组件格式不正确。")
                key = (account, component_name(component))
                accounts[key] += 1
                simid = _identifier(row.get("simid"), "invalid_target_call_extension")
                if simid is not None:
                    extra_values[key].add(simid)

    for path in paths:
        data = _payload(path).lstrip(b"\xef\xbb\xbf \t\r\n")
        if data.startswith(b"<"):
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                fail("unsafe_target_xml", "目标 XML 含 DTD/实体声明，不读取。")
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                fail("invalid_target_xml", "目标 XML 无法解析。")
            if root.tag not in ("smses", "calls"):
                fail(
                    "unknown_target_format",
                    "未知 XML 备份格式；不会猜测字段或日期单位。",
                )
            try:
                if int(root.get("count", "-1")) != len(root):
                    fail("target_count_mismatch", "目标样本 count 与记录数量不一致。")
            except ValueError:
                fail("invalid_target_xml", "目标样本 count 不合法。")
            formats.add("sms-backup-restore")
            observations.append(
                {
                    "format": "sms-backup-restore",
                    "category": "messages" if root.tag == "smses" else "calls",
                    "records": len(root),
                    "encrypted": False,
                }
            )
            for row in root:
                if row.tag == "sms" and root.tag == "smses":
                    required, kind = {"address", "date", "type", "read", "body"}, "sms"
                elif row.tag == "mms" and root.tag == "smses":
                    required, kind = {"date", "msg_box"}, "mms"
                elif row.tag == "call" and root.tag == "calls":
                    required, kind = (
                        {"number", "duration", "date", "type", "presentation"},
                        "calls",
                    )
                else:
                    fail("unknown_target_record", "目标 XML 包含不受支持的记录结构。")
                if not required <= row.attrib.keys():
                    fail(
                        "incomplete_target_record", "目标 XML 缺少格式要求的关键属性。"
                    )
                consume(kind, row.attrib)
        elif data.startswith(b"{"):
            try:
                document = json.loads(data)
            except (ValueError, UnicodeError):
                fail("invalid_target_json", "目标 JSON 无法解析。")
            schema = document.get("schema")
            for kind, row in _json_rows(document):
                consume(kind, row)
            formats.add("native")
            observations.append({"format": schema, "category": "mixed"})
        else:
            fail(
                "unknown_target_format",
                "尚不支持该目标备份格式；厂商专有/加密备份需要独立适配。",
            )
    if len(formats) > 1:
        fail("mixed_target_formats", "提供的目标样本属于不同导入工具，请分别处理。")
    account_list = []
    for (account, component), count in sorted(accounts.items()):
        item = {
            "id": account,
            "component": component,
            "user_id": 0,
            "sub_id": None,
            "association": "unresolved",
            "id_state": "redacted" if any(c in account for c in "[*]") else "exact",
            "sample_count": count,
        }
        extras = extra_values[(account, component)]
        if len(extras) == 1:
            item["call_extras"] = {"simid": next(iter(extras))}
            item["call_extras_evidence"] = "consistent_existing_target_records"
        elif extras:
            item["issue"] = "conflicting_call_simid"
        account_list.append(item)
    return {
        "schema": PROFILE_SCHEMA,
        "version": 1,
        "origin": "target-backup-samples",
        "output_format": next(iter(formats)),
        "observations": observations,
        "category_counts": dict(categories),
        "subscriptions": [
            {"sub_id": value, "logical_slot_index": None, "sample_count": count}
            for value, count in sorted(subscriptions.items())
        ],
        "phone_accounts": account_list,
        "issues": [
            "样本中的账户不保证当前仍然有效。",
            "短信订阅 ID 与通话账户 ID 独立，不自动按数字相同关联。",
        ],
    }


def public_summary(profile):
    """Shareable view: do not expose long account IDs (which may be ICCIDs) or serials."""
    return {
        "origin": profile.get("origin"),
        "status": profile.get("status", "partial"),
        "output_format": profile.get("output_format"),
        "subscriptions": [
            {"sub_id": s["sub_id"], "logical_slot_index": s.get("logical_slot_index")}
            for s in profile.get("subscriptions", [])
        ],
        "phone_accounts": [
            {
                "index": i,
                "component": a["component"],
                "id": "(present)"
                if a.get("id_state", "exact") == "exact"
                else "(redacted)",
                "association": a.get("association"),
            }
            for i, a in enumerate(profile.get("phone_accounts", []))
        ],
        "category_counts": profile.get("category_counts"),
        "issues": profile.get("issues", []),
    }
