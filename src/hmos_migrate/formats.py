from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET
from .common import digest, fail, safe_text, write_json
from .sim import apply_map, validate_map

NATIVE_SCHEMA = "hmos-migrate/native"
NATIVE_MAX_BYTES = 32 * 1024 * 1024


def native_document(records, report, mapping=None):
    mapping = validate_map(mapping)
    result = {
        "schema": NATIVE_SCHEMA,
        "version": 1,
        "date_units": {
            "sms": "milliseconds",
            "calls": "milliseconds",
            "mms": "seconds",
        },
        "conversion": report,
        "sim_map": mapping,
        "records": deepcopy(records),
    }
    result["batch_id"] = digest({"records": records})
    if (
        len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
        > NATIVE_MAX_BYTES
    ):
        fail(
            "native_package_too_large",
            "安装器输入超过 32 MiB；请选择较少的数据类别或使用 XML 导出。",
        )
    return result


def write_native(destination, records, report, mapping=None):
    document = native_document(records, report, mapping)
    write_json(Path(destination) / "migration.json", document)
    return document


def _xml_value(value):
    return "null" if value is None else safe_text(value)


def _save_xml(path, root):
    root.set("count", str(len(root)))
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    ET.fromstring(data)
    with Path(path).open("xb") as stream:
        stream.write(data)
    Path(path).chmod(0o600)
    return hashlib.sha256(data).hexdigest()


def write_sms_backup(destination, records, report, mapping=None, *, unassigned=False):
    mapped = apply_map(records, mapping, unassigned=unassigned)
    header = {
        "backup_set": digest(records)[:32],
        "backup_date": str(int(time.time() * 1000)),
    }
    messages, calls = ET.Element("smses", header), ET.Element("calls", header)
    for item in mapped["sms"]:
        ET.SubElement(
            messages, "sms", {k: _xml_value(v) for k, v in item["values"].items()}
        )
    for item in mapped["calls"]:
        ET.SubElement(
            calls, "call", {k: _xml_value(v) for k, v in item["values"].items()}
        )
    for item in mapped["mms"]:
        attrs = {k: _xml_value(v) for k, v in item["values"].items()}
        attrs["date"] = str(item["values"]["date"] * 1000)
        attrs["date_sent"] = str(item["values"]["date_sent"] * 1000)
        attrs["address"] = next(
            a["address"] for a in item["addresses"] if a["type"] == 137
        )
        mms = ET.SubElement(messages, "mms", attrs)
        parts = ET.SubElement(mms, "parts")
        smil = ET.Element("smil")
        ET.SubElement(smil, "head")
        body = ET.SubElement(smil, "body")
        for part in item["parts"]:
            values = {k: _xml_value(v) for k, v in part["values"].items()}
            if "data" in part:
                values["data"] = part["data"]
            ET.SubElement(parts, "part", values)
            ET.SubElement(
                ET.SubElement(body, "par", {"dur": "5000ms"}),
                "img",
                {"src": values["cl"]},
            )
        ET.SubElement(
            parts,
            "part",
            {
                "seq": "-1",
                "ct": "application/smil",
                "cl": "smil.xml",
                "cid": "<smil>",
                "text": ET.tostring(smil, encoding="unicode"),
            },
        )
        addrs = ET.SubElement(mms, "addrs")
        for address in item["addresses"]:
            ET.SubElement(addrs, "addr", {k: _xml_value(v) for k, v in address.items()})
    files = {}
    if set(report["categories"]) & {"sms", "mms"}:
        files["messages.xml"] = _save_xml(Path(destination) / "messages.xml", messages)
    if "calls" in report["categories"]:
        files["calls.xml"] = _save_xml(Path(destination) / "calls.xml", calls)
    return files
