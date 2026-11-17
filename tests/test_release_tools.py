import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import build_android
import package_release


class ReleaseTests(unittest.TestCase):
    def test_variants_share_identity_and_version_but_not_debug_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifests = []
            for variant in ("standard", "usb"):
                target = Path(temporary) / (variant + ".xml")
                build_android.prepare_manifest(target, variant)
                root = ET.parse(target).getroot()
                ns = "{" + build_android.ANDROID + "}"
                self.assertEqual(
                    root.find("application").get(ns + "debuggable"),
                    str(variant == "usb").lower(),
                )
                self.assertIsNone(root.find("instrumentation"))
                self.assertEqual(
                    root.get(ns + "versionCode"),
                    str(build_android.VERSION["ANDROID_VERSION_CODE"]),
                )
                manifests.append(root)
            self.assertEqual(manifests[0].get("package"), manifests[1].get("package"))

    def test_instrumentation_cannot_enter_standard_variant(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "manifest.xml"
            with self.assertRaises(ValueError):
                build_android.prepare_manifest(target, "standard", True)
            self.assertFalse(target.exists())
            root = build_android.prepare_manifest(target, "usb", True)
            self.assertIsNotNone(root.find("instrumentation"))

    def test_certificate_verification_rejects_different_or_multiple_signers(self):
        expected = "a" * 64
        for output in (
            "",
            "Signer #1 certificate SHA-256 digest: " + "b" * 64,
            "Signer #1 certificate SHA-256 digest: "
            + expected
            + "\nSigner #2 certificate SHA-256 digest: "
            + expected,
        ):
            with (
                self.subTest(output=output),
                patch.object(build_android, "run", return_value=output),
            ):
                with self.assertRaises(ValueError):
                    build_android.verify_apk("signer", "apk", expected)
        with patch.object(
            build_android,
            "run",
            return_value="Signer #1 certificate SHA-256 digest: " + expected,
        ):
            build_android.verify_apk("signer", "apk", expected)

    def test_release_bundle_rejects_development_and_test_apks(self):
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "app.apk"
            import hashlib

            metadata = {
                "package": "dev.hmos.migrate",
                "protocol": 1,
                "version": build_android.VERSION["__version__"],
                "version_code": build_android.VERSION["ANDROID_VERSION_CODE"],
                "variant": "standard",
                "debuggable": False,
                "signing": "release",
                "test_only": False,
                "certificate_sha256": "a" * 64,
                "manifest_sha256": hashlib.sha256(b"synthetic").hexdigest(),
            }
            for change in (
                {"signing": "development"},
                {"test_only": True},
                {"debuggable": True},
                {"version_code": 1},
                {},
            ):
                with self.subTest(change=change):
                    with zipfile.ZipFile(apk, "w") as z:
                        z.writestr("AndroidManifest.xml", b"synthetic")
                        z.writestr(
                            "assets/hmos-migrate.json", json.dumps(metadata | change)
                        )
                    with patch.object(package_release, "verify_apk"):
                        if change:
                            with self.assertRaises(ValueError):
                                package_release.validate_apk(
                                    apk, "standard", "signer", "a" * 64
                                )
                        else:
                            package_release.validate_apk(
                                apk, "standard", "signer", "a" * 64
                            )

    def test_release_identity_cannot_be_generated_inside_source(self):
        spec = importlib.util.spec_from_file_location(
            "init_signing", TOOLS / "init_signing.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(
            sys, "argv", ["init_signing", "--out-dir", str(TOOLS / "forbidden-key")]
        ):
            with patch.object(module, "run") as command, self.assertRaises(SystemExit):
                module.main()
            command.assert_not_called()
