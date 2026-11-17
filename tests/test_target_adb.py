import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import xml.etree.ElementTree as ET
from hmos_migrate.adb import Adb, parse_subscriptions, parse_phone_accounts
from hmos_migrate.common import MigrationError
from hmos_migrate.target import analyze_samples
from hmos_migrate.sim import map_from_profile

SMS = b'<smses count="1"><sms address="+15550000000" date="1700000000000" type="1" read="1" body="synthetic" sub_id="101"/></smses>'
CALLS = b'<calls count="1"><call number="+15550000000" date="1700000000000" type="2" duration="4" presentation="1" subscription_id="account-alpha" subscription_component_name="com.example.phone/.ConnectionService" simid="909"/></calls>'


class TargetTests(unittest.TestCase):
    def test_json_identifiers_never_coerce_fractions_or_booleans(self):
        with tempfile.TemporaryDirectory() as t:
            sample = Path(t) / "sample.json"
            for category, xml, field, code in (
                ("sms", SMS, "sub_id", "invalid_target_subscription"),
                ("calls", CALLS, "simid", "invalid_target_call_extension"),
            ):
                for value in (101.9, True, False, "101.9", [], {}, 2147483648):
                    with self.subTest(category=category, value=value):
                        row = dict(ET.fromstring(xml)[0].attrib)
                        row[field] = value
                        sample.write_text(
                            json.dumps(
                                {
                                    "schema": "hmos-migrate/provider-backup",
                                    "version": 1,
                                    category: [row],
                                }
                            )
                        )
                        with self.assertRaises(MigrationError) as error:
                            analyze_samples([sample])
                        self.assertEqual(error.exception.code, code)

    def test_json_unknown_or_incomplete_structure_is_not_empty_backup(self):
        invalid = [
            {
                "schema": "hmos-migrate/native",
                "version": 1,
                "records": {"vendor_sms": [{"body": "ignored"}]},
            },
            {
                "schema": "hmos-migrate/provider-backup",
                "version": 1,
                "sms": [{"vendor_specific_record": "unknown"}],
            },
            {"schema": "hmos-migrate/provider-backup", "version": 1},
            {"schema": "hmos-migrate/provider-backup", "version": 1, "sms": None},
            {"schema": "hmos-migrate/provider-backup", "version": 1, "sms": [None]},
            {
                "schema": "hmos-migrate/provider-backup",
                "version": 1,
                "sms": [],
                "vendor_sms": [],
            },
            {"schema": "hmos-migrate/provider-backup", "version": True, "sms": []},
        ]
        with tempfile.TemporaryDirectory() as t:
            sample = Path(t) / "sample.json"
            for document in invalid:
                with self.subTest(document=document):
                    sample.write_text(json.dumps(document))
                    with self.assertRaises(MigrationError):
                        analyze_samples([sample])

    def test_provider_json_single_category_and_explicit_empty(self):
        with tempfile.TemporaryDirectory() as t:
            sample = Path(t) / "sample.json"
            for category, xml in (("sms", SMS), ("calls", CALLS)):
                row = dict(ET.fromstring(xml)[0].attrib)
                for rows in ([row], []):
                    with self.subTest(category=category, empty=not rows):
                        sample.write_text(
                            json.dumps(
                                {
                                    "schema": "hmos-migrate/provider-backup",
                                    "version": 1,
                                    category: rows,
                                }
                            )
                        )
                        result = analyze_samples([sample])
                        self.assertEqual(result["output_format"], "native")
                        self.assertEqual(
                            result["category_counts"].get(category, 0), len(rows)
                        )
                        if category == "sms":
                            self.assertEqual(result["phone_accounts"], [])
                        else:
                            self.assertEqual(result["subscriptions"], [])
                        if rows and category == "sms":
                            self.assertEqual(result["subscriptions"][0]["sub_id"], 101)

    def make_apk(self, path):
        manifest = b"synthetic manifest for mocked ADB only"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("AndroidManifest.xml", manifest)
            z.writestr(
                "assets/hmos-migrate.json",
                json.dumps(
                    {
                        "package": "dev.hmos.migrate",
                        "protocol": 1,
                        "debuggable": True,
                        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                    }
                ),
            )

    def test_wrong_apk_rejected_before_connecting(self):
        with tempfile.TemporaryDirectory() as t:
            apk = Path(t) / "wrong.apk"
            with zipfile.ZipFile(apk, "w") as z:
                z.writestr("other.txt", "synthetic")
            bridge = Adb(serial="test")
            with patch.object(bridge, "select") as select:
                with self.assertRaises(MigrationError) as error:
                    bridge.install(apk)
                self.assertEqual(error.exception.code, "invalid_installer_apk")
                select.assert_not_called()

    def test_active_phone_batch_prevents_reinstallation(self):
        with tempfile.TemporaryDirectory() as t:
            apk = Path(t) / "installer.apk"
            self.make_apk(apk)
            bridge = Adb(serial="test")
            with (
                patch.object(bridge, "select", return_value=bridge),
                patch.object(
                    bridge,
                    "_run",
                    side_effect=[
                        b"package:/data/app/installer.apk\n",
                        b'{"run_id":"active"}',
                    ],
                ) as run,
            ):
                with self.assertRaises(MigrationError) as error:
                    bridge.install(apk)
                self.assertEqual(error.exception.code, "active_phone_batch")
                self.assertTrue(
                    all(call.args[0][0] != "install" for call in run.call_args_list)
                )

    def test_identical_installer_is_not_reinstalled(self):
        with tempfile.TemporaryDirectory() as t:
            apk = Path(t) / "installer.apk"
            self.make_apk(apk)
            bridge = Adb(serial="test")
            checksum = hashlib.sha256(apk.read_bytes()).hexdigest().encode()
            with (
                patch.object(bridge, "select", return_value=bridge),
                patch.object(
                    bridge,
                    "_run",
                    side_effect=[
                        b"package:/data/app/installer.apk\n",
                        b"cat: No such file",
                        checksum + b" installer.apk\n",
                    ],
                ) as run,
            ):
                bridge.install(apk)
                self.assertTrue(
                    all(call.args[0][0] != "install" for call in run.call_args_list)
                )

    def test_each_sample_independent_and_no_false_association(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            (p / "sms.bin").write_bytes(SMS)
            (p / "calls.bin").write_bytes(CALLS)
            sms = analyze_samples([p / "sms.bin"])
            calls = analyze_samples([p / "calls.bin"])
            both = analyze_samples([p / "sms.bin", p / "calls.bin"])
            self.assertEqual(sms["phone_accounts"], [])
            self.assertEqual(calls["subscriptions"], [])
            self.assertIsNone(both["phone_accounts"][0]["sub_id"])
            self.assertEqual(both["phone_accounts"][0]["id"], "account-alpha")
            self.assertEqual(both["phone_accounts"][0]["call_extras"]["simid"], 909)
            self.assertNotIn("synthetic", json.dumps(both))

    def test_zip_and_empty_samples(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "sms.xml.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("sms.xml", SMS)
            self.assertEqual(
                analyze_samples([p])["output_format"], "sms-backup-restore"
            )
            empty = Path(t) / "empty.xml"
            empty.write_text('<calls count="0"/>')
            self.assertEqual(analyze_samples([empty])["phone_accounts"], [])

    def test_unknown_and_entity_formats_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "sample"
            for data in (
                b"not-a-backup",
                b"<vendor-backup/>",
                b'<!DOCTYPE smses [<!ENTITY x "secret">]><smses count="0"/>',
                b'{"sms":[]}',
            ):
                p.write_bytes(data)
                with self.assertRaises(MigrationError):
                    analyze_samples([p])

    def test_old_and_current_subscription_formats(self):
        a = parse_subscriptions("sSlotIndexToSubId[0]: subId=2\n")
        b = parse_subscriptions("sSlotIndexToSubId[0]: subIds=0=[2]\n")
        c = parse_subscriptions(
            "Logical SIM slot 0: subId=2\nLocal log:\n Logical SIM slot 1: subId=9"
        )
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertEqual(
            len(parse_subscriptions("sSlotIndexToSubId[0]: subIds=0=[2, 3]")), 2
        )
        with self.assertRaises(MigrationError):
            parse_subscriptions("sSlotIndexToSubId[0]: subIds=1=[2]")

    def test_accounts_only_from_current_registry(self):
        text = """PhoneAccountRegistrar:
  phoneAccounts:
    [[X] PhoneAccount: ComponentInfo{com.example.phone/.ConnectionService}, account-alpha, UserHandle{0} Capabilities: SimSub]
  defaultOutgoing:
    [[X] PhoneAccount: ComponentInfo{com.example.phone/.ConnectionService}, stale, UserHandle{0} Capabilities: SimSub]
"""
        accounts = parse_phone_accounts(text)
        self.assertEqual(len(accounts), 1)
        self.assertIsNone(accounts[0]["sub_id"])

    def test_multiple_devices_never_selects_first(self):
        response = subprocess.CompletedProcess(
            [], 0, b"List of devices attached\na device\nb unauthorized\n", b""
        )
        with patch("subprocess.run", return_value=response):
            with self.assertRaises(MigrationError) as error:
                Adb().select()
            self.assertEqual(error.exception.code, "choose_device")

    def test_device_command_errors_do_not_leak_raw_output(self):
        response = subprocess.CompletedProcess(
            [], 1, b"secret-phone-body", b"secret-iccid"
        )
        with patch("subprocess.run", return_value=response):
            with self.assertRaises(MigrationError) as error:
                Adb(serial="chosen").shell(["getprop", "x"])
            self.assertNotIn("secret", str(error.exception))

    def test_negative_index_does_not_select_last_sim(self):
        profile = {
            "schema": "hmos-migrate/target",
            "version": 1,
            "subscriptions": [{"sub_id": 101}, {"sub_id": 203}],
            "phone_accounts": [],
        }
        with self.assertRaises(MigrationError):
            map_from_profile(profile, {"0": -1})

    def test_equal_numeric_ids_are_not_proof_of_association(self):
        profile = {
            "schema": "hmos-migrate/target",
            "version": 1,
            "subscriptions": [{"sub_id": 101}],
            "phone_accounts": [
                {
                    "id": "101",
                    "component": "example/Service",
                    "user_id": 0,
                    "sub_id": None,
                    "association": "unresolved",
                }
            ],
        }
        self.assertNotIn(
            "phone_account", map_from_profile(profile, {"0": 0})["slots"]["0"]
        )
        profile["phone_accounts"][0].update(sub_id=101, association="android_api")
        self.assertEqual(
            map_from_profile(profile, {"0": 0})["slots"]["0"]["phone_account"]["id"],
            "101",
        )


if __name__ == "__main__":
    unittest.main()
