import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from hmos_migrate.cli import convert
from hmos_migrate.common import MigrationError
from hmos_migrate.sim import validate_map
from hmos_migrate.formats import native_document
from synthetic import make_backup, PASSWORD, MAP


class ConversionTests(unittest.TestCase):
    def test_generated_native_sample_selects_installer_format(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            convert(source, root / "sample", password=PASSWORD)
            report = convert(
                source,
                root / "result",
                password=PASSWORD,
                output_format="target",
                target_samples=[root / "sample/migration.json"],
            )
            self.assertEqual(report["output_format"], "native")
            data = json.loads((root / "result/migration.json").read_text())
            self.assertEqual(len(data["records"]["sms"]), 1)
            self.assertEqual(len(data["records"]["calls"]), 1)

    def test_target_samples_select_format_without_copying_target_records(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        sms = examples / "target-sms.example.xml"
        calls = examples / "target-calls.example.xml"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            for i, samples in enumerate(([sms], [calls], [sms, calls])):
                with self.subTest(samples=[p.name for p in samples]):
                    output = root / str(i)
                    report = convert(
                        source,
                        output,
                        password=PASSWORD,
                        output_format="target",
                        target_samples=samples,
                        mapping=MAP,
                    )
                    self.assertEqual(report["output_format"], "sms-backup-restore")
                    self.assertEqual(report["counts"], {"sms": 1, "calls": 1, "mms": 0})
                    messages = ET.parse(output / "messages.xml").getroot()
                    call_rows = ET.parse(output / "calls.xml").getroot()
                    self.assertEqual(len(messages), 1)
                    self.assertEqual(len(call_rows), 1)
                    self.assertEqual(messages[0].get("sub_id"), "101")
                    self.assertEqual(
                        call_rows[0].get("subscription_id"), "account-alpha"
                    )
                    for artifact in output.iterdir():
                        data = artifact.read_text()
                        self.assertNotIn("Synthetic example only", data)
                        self.assertNotIn("+15550000001", data)

    def test_unknown_target_stops_before_password_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            sample = root / "vendor.bin"
            sample.write_bytes(b"unknown-encrypted-vendor-format")
            with patch("hmos_migrate.cli.getpass.getpass") as prompt:
                with self.assertRaises(MigrationError) as error:
                    convert(
                        source,
                        root / "result",
                        output_format="target",
                        target_samples=[sample],
                    )
                self.assertEqual(error.exception.code, "unknown_target_format")
                prompt.assert_not_called()
            self.assertFalse((root / "result").exists())

    def test_empty_tables_and_explicit_native_unassigned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, empty=True)
            report = convert(
                source, root / "result", password=PASSWORD, unassigned=True
            )
            self.assertEqual(report["counts"], {"sms": 0, "calls": 0, "mms": 0})
            self.assertTrue(report["complete"])

    def test_encrypted_multivolume_wal_to_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, mms=True, duplicates=True, split=True)
            report = convert(source, root / "result", password=PASSWORD)
            data = json.loads((root / "result/migration.json").read_text())
            self.assertEqual(report["counts"], {"sms": 2, "calls": 1, "mms": 1})
            self.assertEqual(
                data["records"]["sms"][0]["values"]["body"], "合成消息 <&>\n🙂"
            )
            self.assertNotEqual(
                data["records"]["sms"][0]["source_id"],
                data["records"]["sms"][1]["source_id"],
            )
            self.assertEqual(data["records"]["mms"][0]["values"]["date"], 1700000500)
            self.assertIsNone(data["sim_map"])
            self.assertTrue((root / "result/READY").exists())

    def test_xml_mapping_is_not_slot_plus_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, mms=True)
            convert(
                source,
                root / "result",
                password=PASSWORD,
                output_format="sms-backup-restore",
                mapping=MAP,
            )
            messages = ET.parse(root / "result/messages.xml").getroot()
            call = ET.parse(root / "result/calls.xml").getroot()[0]
            self.assertEqual(messages.find("sms").get("sub_id"), "101")
            self.assertEqual(messages.find("mms").get("sub_id"), "203")
            self.assertEqual(messages.find("mms").get("date"), "1700000500000")
            self.assertEqual(call.get("subscription_id"), "account-alpha")
            self.assertEqual(call.get("simid"), "909")

    def test_calls_only_and_time_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, sms=False, zero_date=True)
            mapping = copy.deepcopy(MAP)
            mapping["slots"] = {"0": mapping["slots"]["0"]}
            del mapping["slots"]["0"]["sub_id"]
            report = convert(
                source,
                root / "result",
                password=PASSWORD,
                output_format="sms-backup-restore",
                mapping=mapping,
            )
            self.assertFalse((root / "result/messages.xml").exists())
            self.assertEqual(
                ET.parse(root / "result/calls.xml").getroot()[0].get("date"),
                "1700000099000",
            )
            self.assertEqual(len(report["repairs"]), 1)

    def test_sms_only_does_not_require_call_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, calls=False)
            mapping = {
                "schema": "hmos-migrate/sim-map",
                "version": 1,
                "slots": {"0": {"sub_id": 101}},
            }
            report = convert(
                source,
                root / "result",
                password=PASSWORD,
                mapping=mapping,
                output_format="sms-backup-restore",
            )
            self.assertEqual(report["counts"]["sms"], 1)
            self.assertFalse((root / "result/calls.xml").exists())

    def test_partial_mms_is_blocked_unless_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, mms=True, missing_mms=True)
            with self.assertRaises(MigrationError) as error:
                convert(source, root / "result", password=PASSWORD)
            self.assertEqual(error.exception.code, "partial_conversion_blocked")
            self.assertFalse((root / "result").exists())
            self.assertTrue((root / "result.failure.json").exists())
            report = convert(
                source, root / "allowed", password=PASSWORD, allow_partial=True
            )
            self.assertFalse(report["complete"])
            self.assertFalse(
                json.loads((root / "allowed/migration.json").read_text())["conversion"][
                    "complete"
                ]
            )

    def test_rcs_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, rcs=True)
            with self.assertRaises(MigrationError):
                convert(source, root / "result", password=PASSWORD)

    def test_wrong_password_and_corrupt_file_no_import_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            with self.assertRaises(MigrationError):
                convert(source, root / "bad", password=b"wrong")
            self.assertFalse((root / "bad").exists())
            volume = next(source.rglob("part.0.tar"))
            data = bytearray(volume.read_bytes())
            data[-1] ^= 1
            volume.write_bytes(data)
            with self.assertRaises(MigrationError):
                convert(source, root / "bad2", password=PASSWORD)
            self.assertFalse((root / "bad2").exists())

    def test_no_mapping_requires_explicit_unassigned_for_xml(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            with self.assertRaises(MigrationError) as error:
                convert(
                    source,
                    root / "bad",
                    password=PASSWORD,
                    output_format="sms-backup-restore",
                )
            self.assertEqual(error.exception.code, "missing_sim_mapping")
            convert(
                source,
                root / "good",
                password=PASSWORD,
                output_format="sms-backup-restore",
                unassigned=True,
            )
            self.assertEqual(
                ET.parse(root / "good/messages.xml").getroot()[0].get("sub_id"), "-1"
            )

    def test_existing_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            out = root / "keep"
            out.mkdir()
            (out / "user.txt").write_text("keep")
            with self.assertRaises(MigrationError):
                convert(source, out, password=PASSWORD)
            self.assertEqual((out / "user.txt").read_text(), "keep")

    def test_same_target_requires_explicit_merge(self):
        mapping = copy.deepcopy(MAP)
        mapping["slots"]["1"]["sub_id"] = 101
        with self.assertRaises(MigrationError):
            validate_map(mapping)
        mapping["allow_merge"] = True
        validate_map(mapping)

    def test_call_only_merge_also_requires_confirmation(self):
        mapping = copy.deepcopy(MAP)
        for entry in mapping["slots"].values():
            entry.pop("sub_id")
            entry.pop("call_extras", None)
        mapping["slots"]["1"]["phone_account"] = mapping["slots"]["0"]["phone_account"]
        with self.assertRaises(MigrationError):
            validate_map(mapping)

    def test_manifest_missing_volume_is_corruption_not_empty_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root, split=True)
            next(source.rglob("part.1.tar")).unlink()
            with self.assertRaises(MigrationError) as error:
                convert(source, root / "result", password=PASSWORD)
            self.assertEqual(error.exception.code, "manifest_file_missing")
            self.assertFalse((root / "result").exists())

    def test_nonhidden_info_json_is_metadata_not_ciphertext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_backup(root)
            for p in source.rglob(".info.json"):
                p.rename(p.with_name("info.json"))
            self.assertTrue(
                convert(source, root / "result", password=PASSWORD)["complete"]
            )

    def test_native_limit_checks_actual_pretty_json_bytes(self):
        records = {
            "sms": [
                {"source_id": "0" * 64, "source_slot": 0, "values": {"body": "x" * 100}}
            ],
            "calls": [],
            "mms": [],
        }
        report = {"complete": True}
        document = native_document(records, report)
        size = len(json.dumps(document, ensure_ascii=False, indent=2).encode())
        with patch("hmos_migrate.formats.NATIVE_MAX_BYTES", size - 1):
            with self.assertRaises(MigrationError):
                native_document(records, report)


if __name__ == "__main__":
    unittest.main()
