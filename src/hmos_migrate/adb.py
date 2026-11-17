import json
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid
import zipfile
from .common import fail, read_json
from .sim import PROFILE_SCHEMA

APP_ID = "dev.hmos.migrate"


def parse_subscriptions(text):
    found = {}
    for line in text.splitlines():
        # Diagnostic logs must never be treated as current state.
        if (
            line.strip()
            .lower()
            .startswith(("local log", "local logs", "mLocalLog".lower()))
        ):
            break
        match = re.fullmatch(r"\s*Logical SIM slot (\d+): subId=(\d+)\s*", line)
        if match:
            slot, ids = int(match[1]), [int(match[2])]
        else:
            match = re.fullmatch(r"\s*sSlotIndexToSubId\[(\d+)\]: subId=(\d+)\s*", line)
            if match:
                slot, ids = int(match[1]), [int(match[2])]
            else:
                match = re.fullmatch(
                    r"\s*sSlotIndexToSubId\[(\d+)\]: subIds=(\d+)=\[([\d, ]+)\]\s*",
                    line,
                )
                if not match:
                    continue
                slot = int(match[1])
                if slot != int(match[2]):
                    fail("conflicting_subscription_dump", "系统卡槽映射文本内部矛盾。")
                ids = [int(v.strip()) for v in match[3].split(",")]
        if slot in found and found[slot] != ids:
            fail("conflicting_subscription_dump", "系统返回了互相矛盾的当前订阅信息。")
        found[slot] = ids
    return [
        {"logical_slot_index": slot, "sub_id": sub, "evidence": "adb_current_slot_map"}
        for slot, ids in sorted(found.items())
        for sub in ids
    ]


def parse_phone_accounts(text):
    accounts = []
    in_accounts = False
    section_indent = 0
    for line in text.splitlines():
        if line.strip().startswith("phoneAccounts:"):
            in_accounts = True
            section_indent = len(line) - len(line.lstrip())
            continue
        if (
            in_accounts
            and line.strip()
            and len(line) - len(line.lstrip()) <= section_indent
        ):
            in_accounts = False
        # Only actual enabled SIM PhoneAccount entries, never historical call events.
        if (
            not in_accounts
            or not re.match(r"\s*\[\[X\]\s+PhoneAccount:", line)
            or "SimSub" not in line
        ):
            continue
        match = re.search(
            r"PhoneAccount:\s*ComponentInfo\{([^}]+)\},\s*(.*?),\s*UserHandle\{(\d+)\}",
            line,
        )
        if not match:
            continue
        component, account, user = match[1], match[2], int(match[3])
        if user != 0:
            continue
        item = {
            "component": component,
            "id": account,
            "user_id": user,
            "sub_id": None,
            "association": "unresolved",
            "enabled": True,
            "id_state": "redacted" if any(c in account for c in "[*]") else "exact",
        }
        if item not in accounts:
            accounts.append(item)
    return accounts


class Adb:
    def __init__(self, executable="adb", serial=None, timeout=20):
        self.executable, self.serial, self.timeout = executable, serial, timeout

    def _run(self, args, *, selected=True, data=None, allow_missing=False):
        command = [self.executable]
        if selected:
            if self.serial is None:
                fail("device_not_selected", "先明确选择目标设备。")
            command += ["-s", self.serial]
        try:
            p = subprocess.run(
                command + args, input=data, capture_output=True, timeout=self.timeout
            )
        except FileNotFoundError:
            fail(
                "adb_not_found",
                "没有找到 adb，请安装 Android SDK Platform-Tools 或指定 --adb。",
            )
        except subprocess.TimeoutExpired:
            fail("adb_timeout", "ADB 操作超时，请检查连接与手机授权。")
        if p.returncode:
            if allow_missing:
                return p.stdout + p.stderr
            if b"INSTALL_FAILED_UPDATE_INCOMPATIBLE" in p.stdout + p.stderr:
                fail(
                    "apk_signature_mismatch",
                    "已安装版本与此 APK 签名不同。先导出应用里的备份，不要自动清除其数据。",
                )
            if b"INSTALL_FAILED_OLDER_SDK" in p.stdout + p.stderr:
                fail(
                    "unsupported_android_version",
                    "自写安装器需要 Android 12/API 31 以上；可使用 XML 路径。",
                )
            fail(
                "adb_command_failed",
                "ADB 命令失败；请确认设备已授权、工具可用，未输出原始日志中的个人信息。",
            )
        return p.stdout

    def select(self):
        output = self._run(["devices", "-l"], selected=False).decode(errors="replace")
        devices = []
        for line in output.splitlines():
            if not line or line.startswith(("List of", "*")):
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
        if self.serial is None:
            if len(devices) != 1:
                fail(
                    "choose_device",
                    "未检测到唯一设备；多设备时必须显式指定 --serial，不能自动选择。",
                )
            self.serial = devices[0][0]
        matches = [d for d in devices if d[0] == self.serial]
        if len(matches) != 1 or matches[0][1] != "device":
            fail("device_unavailable", "所选设备不存在、离线或尚未允许 USB 调试。")
        if self.shell(["getprop", "sys.boot_completed"]).strip() != "1":
            fail("device_not_booted", "设备尚未完成启动。")
        if self.shell(["am", "get-current-user"]).strip() != "0":
            fail("unsupported_user", "仅支持主用户；请退出工作资料或次要用户。")
        return self

    def shell(self, args):
        return self._run(["shell", shlex.join(args)]).decode(errors="replace")

    def discover(self):
        self.select()
        subscriptions = parse_subscriptions(self.shell(["dumpsys", "isub"]))
        accounts = parse_phone_accounts(self.shell(["dumpsys", "telecom"]))
        return {
            "schema": PROFILE_SCHEMA,
            "version": 1,
            "origin": "adb-diagnostics",
            "output_format": None,
            "device": {
                "manufacturer": self.shell(
                    ["getprop", "ro.product.manufacturer"]
                ).strip(),
                "model": self.shell(["getprop", "ro.product.model"]).strip(),
                "sdk": self.shell(["getprop", "ro.build.version.sdk"]).strip(),
            },
            "subscriptions": subscriptions,
            "phone_accounts": accounts,
            "issues": [
                "诊断文本仅提供候选；不会假设电话账户 ID 等于短信订阅 ID。",
                "使用 --apk 的公开 API 探测或手动确认账户关联。",
            ],
        }

    def install(self, apk):
        try:
            with zipfile.ZipFile(apk) as archive:
                if (
                    archive.getinfo("assets/hmos-migrate.json").file_size > 8192
                    or archive.getinfo("AndroidManifest.xml").file_size > 1024 * 1024
                ):
                    fail("invalid_installer_apk", "安装器元数据超出限制。")
                metadata = json.loads(archive.read("assets/hmos-migrate.json"))
                if metadata.get("test_only", False):
                    fail(
                        "invalid_installer_apk",
                        "这是模拟器测试包，请选择发行 USB 版 APK。",
                    )
                if (
                    metadata.get("package") == APP_ID
                    and metadata.get("variant") == "standard"
                ):
                    fail(
                        "standard_variant_requires_manual_import",
                        "普通版只支持手动选文件；电脑探测/传入请选择同版本 USB 版 APK。",
                    )
                if (
                    metadata.get("package") != APP_ID
                    or metadata.get("protocol") != 1
                    or metadata.get("debuggable") is not True
                ):
                    fail(
                        "invalid_installer_apk",
                        "这不是匹配当前 USB 协议的 USB 版安装器。",
                    )
                if (
                    metadata.get("manifest_sha256")
                    != hashlib.sha256(archive.read("AndroidManifest.xml")).hexdigest()
                ):
                    fail("invalid_installer_apk", "APK 清单校验失败。")
        except (zipfile.BadZipFile, KeyError, ValueError, OSError):
            fail(
                "invalid_installer_apk",
                "请选择由配套构建脚本生成的自写 APK，不是第三方恢复软件 APK。",
            )
        self.select()
        installed = self._run(["shell", "pm", "path", APP_ID], allow_missing=True)
        if installed.startswith(b"package:"):
            active = self._run(
                ["exec-out", "run-as", APP_ID, "cat", "files/active.json"],
                allow_missing=True,
            )
            if active.lstrip().startswith(b"run-as:") and b"not debuggable" in active:
                fail(
                    "private_usb_channel_unavailable",
                    "已安装版本不开放调试私有通道，请在手机手动选文件。",
                )
            try:
                config = json.loads(active)
            except (ValueError, UnicodeError):
                config = None
            if isinstance(config, dict):
                fail(
                    "active_phone_batch",
                    "手机安装器还有活动批次。先完成/恢复默认短信并结束批次，USB 操作不会重装打断它。",
                )
            paths = [
                line[8:]
                for line in installed.decode(errors="replace").splitlines()
                if line.startswith("package:")
            ]
            if len(paths) == 1:
                remote = (
                    self._run(["shell", "sha256sum", paths[0]], allow_missing=True)
                    .decode(errors="replace")
                    .split()
                )
                if (
                    remote
                    and remote[0] == hashlib.sha256(Path(apk).read_bytes()).hexdigest()
                ):
                    return
        try:
            sdk = int(self.shell(["getprop", "ro.build.version.sdk"]).strip())
        except ValueError:
            fail("unknown_android_version", "无法确认目标 Android 版本。")
        if sdk < 31:
            fail(
                "unsupported_android_version",
                "自写安装器需要 Android 12/API 31 以上；诊断读取与 XML 路径仍可单独使用。",
            )
        # A debug package permits the private run-as USB channel. No permission grants.
        previous = self.timeout
        try:
            self.timeout = max(self.timeout, 120)
            self._run(["install", "-r", "-t", str(Path(apk).resolve())])
        finally:
            self.timeout = previous

    def probe(self, apk, wait_seconds=120):
        self.install(apk)
        nonce = uuid.uuid4().hex
        self.shell(
            [
                "am",
                "start",
                "-n",
                APP_ID + "/.MainActivity",
                "--ez",
                "probe_only",
                "true",
                "--es",
                "probe_nonce",
                nonce,
            ]
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            data = self._run(
                ["exec-out", "run-as", APP_ID, "cat", "files/device-profile.json"],
                allow_missing=True,
            )
            try:
                profile = json.loads(data)
                if (
                    profile.get("schema") == PROFILE_SCHEMA
                    and profile.get("version") == 1
                    and profile.get("request_id") == nonce
                ):
                    return profile
            except (ValueError, UnicodeError):
                pass
            time.sleep(1)
        fail(
            "probe_needs_phone_action",
            "请在手机安装器中授权“读取 SIM 信息”并点击刷新，然后重试。",
        )

    def deliver(self, apk, package):
        document = read_json(package)
        if (
            document.get("schema") != "hmos-migrate/native"
            or document.get("version") != 1
        ):
            fail("invalid_native_package", "不是受支持的安装器迁移文件。")
        self.install(apk)
        self.shell(["run-as", APP_ID, "mkdir", "-p", "files"])
        name = "incoming-" + uuid.uuid4().hex + ".json"
        command = "run-as " + APP_ID + " sh -c " + shlex.quote("cat > files/" + name)
        data = Path(package).read_bytes()
        self._run(["shell", "-T", command], data=data)
        returned = self._run(["exec-out", "run-as", APP_ID, "cat", "files/" + name])
        if data != returned:
            fail("usb_copy_mismatch", "USB 文件校验失败，未启动导入。")
        self.shell(
            ["am", "start", "-n", APP_ID + "/.MainActivity", "--es", "load_usb", name]
        )
