import argparse
import getpass
import json
from pathlib import Path
import tempfile
from .adb import Adb
from .backup import decrypt_backup, inspect_backup
from .common import MigrationError, fail, private_umask, read_json, write_json
from .formats import write_native, write_sms_backup
from .records import load_records
from .sim import MAP_SCHEMA, map_from_profile, validate_map
from .target import analyze_samples, public_summary


def convert(
    source,
    output,
    *,
    output_format="native",
    categories=None,
    mapping=None,
    unassigned=False,
    allow_partial=False,
    password=None,
    target_samples=None,
):
    source, output = Path(source), Path(output)
    if output.exists():
        fail("output_exists", "输出目录已经存在，请选择新目录。")
    inspection = inspect_backup(source)
    if categories is None:
        categories = (
            {"sms", "mms"} if inspection["modules_present"]["sms"] else set()
        ) | ({"calls"} if inspection["modules_present"]["calls"] else set())
    categories = set(categories)
    if not categories or categories - {"sms", "calls", "mms"}:
        fail("invalid_categories", "选择 sms、calls、mms 中至少一类数据。")
    target = analyze_samples(target_samples) if target_samples else None
    if output_format == "target":
        if target is None:
            fail("missing_target_sample", "--format target 需要目标短信或通话样本。")
        output_format = target["output_format"]
    if output_format not in ("native", "sms-backup-restore"):
        fail("unsupported_output_format", "未知输出格式。")
    if password is None:
        password = getpass.getpass("鸿蒙备份密码（输入隐藏）：").encode("utf-8")
    validate_map(mapping)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".hmos-work-", dir=output.parent
    ) as temporary:
        work = Path(temporary)
        authenticated = decrypt_backup(source, work / "decrypted", password, categories)
        records, report = load_records(work / "decrypted", work, categories)
        report.update(
            {
                "schema": "hmos-migrate/conversion-report",
                "version": 1,
                "inspection": inspection,
                "authenticated_files": authenticated,
                "output_format": output_format,
                "status": "complete" if report["complete"] else "partial",
                "device_restore_verified": False,
            }
        )
        if not report["complete"] and not allow_partial:
            failure = output.with_name(output.name + ".failure.json")
            if not failure.exists():
                write_json(failure, report)
            fail(
                "partial_conversion_blocked",
                "有未支持或缺失的数据；未生成可导入文件。查看失败报告，确认后可加 --allow-partial。",
            )
        published = work / "ready"
        published.mkdir(mode=0o700)
        if output_format == "native":
            if unassigned and report["source_slots"]:
                mapping = (
                    json.loads(json.dumps(mapping))
                    if mapping
                    else {"schema": MAP_SCHEMA, "version": 1, "slots": {}}
                )
                for slot in report["source_slots"]:
                    mapping["slots"].setdefault(slot, {"unassigned": True})
            document = write_native(published, records, report, mapping)
            report["batch_id"] = document["batch_id"]
            report["sim_mapping"] = (
                "provided_revalidate_on_device" if mapping else "choose_in_installer"
            )
        else:
            report["files_sha256"] = write_sms_backup(
                published, records, report, mapping, unassigned=unassigned
            )
            report["sim_mapping"] = "provided" if mapping else "explicitly_unassigned"
        if target is not None:
            write_json(published / "target-profile.json", target)
            report["target_sample_summary"] = public_summary(target)
        write_json(published / "report.json", report)
        (published / "READY").write_text(
            "Conversion completed; inspect report.json before importing.\n"
        )
        if output.exists():
            fail("output_exists", "输出目录在转换期间被创建，未覆盖。")
        published.rename(output)
    return report


def interactive_map(profile, source_slots):
    subscriptions = profile.get("subscriptions", [])
    accounts = profile.get("phone_accounts", [])
    if not subscriptions and not accounts:
        fail(
            "no_target_identifiers",
            "样本没有有效卡标识，不能从空备份猜测。请使用 USB/安装器读取或手动填写。",
        )
    print("目标订阅（这是系统内部 ID，不是电话号码）：")
    for i, sub in enumerate(subscriptions):
        print(
            f"  {i}: sub_id={sub['sub_id']}，逻辑槽={sub.get('logical_slot_index', '未知')}"
        )
    result = {"schema": MAP_SCHEMA, "version": 1, "allow_merge": False, "slots": {}}
    for source in source_slots:
        if subscriptions:
            answer = input(
                f"源槽 {source} 对应哪个目标订阅序号？输入 c 仅映射通话，u 不保留归属："
            ).strip()
        else:
            print(f"源槽 {source}：样本仅提供电话账户，将只建立通话映射。")
            answer = "c"
        if answer == "u":
            result["slots"][source] = {"unassigned": True}
            continue
        entry = (
            {}
            if answer == "c"
            else map_from_profile(profile, {source: int(answer)})["slots"][source]
        )
        if "phone_account" not in entry and accounts:
            print("电话账户关联尚未自动证实。请确认后选择；输入 s 跳过（仅短信可用）。")
            for i, account in enumerate(accounts):
                identifier = account.get("id", "")
                label = identifier if len(identifier) <= 8 else "…" + identifier[-6:]
                print(
                    f"  {i}: {account.get('label', account['component'])}，ID={json.dumps(label, ensure_ascii=False)}，状态={account.get('id_state', 'exact')}"
                )
            selected = input(
                "电话账户序号（s 暂不填；u 明确不保留此来源组的卡归属）："
            ).strip()
            if selected == "u":
                result["slots"][source] = {"unassigned": True}
                continue
            if selected != "s":
                index = int(selected)
                if index < 0 or index >= len(accounts):
                    fail("invalid_target_choice", "目标电话账户序号不存在。")
                account = accounts[index]
                entry["phone_account"] = {
                    k: account[k] for k in ("id", "component", "user_id")
                }
                if account.get("call_extras"):
                    entry["call_extras"] = account["call_extras"]
        result["slots"][source] = entry
    sub_ids = [x["sub_id"] for x in result["slots"].values() if "sub_id" in x]
    phone_ids = [
        (x["phone_account"]["id"], x["phone_account"]["component"])
        for x in result["slots"].values()
        if "phone_account" in x
    ]
    if len(sub_ids) != len(set(sub_ids)) or len(phone_ids) != len(set(phone_ids)):
        result["allow_merge"] = (
            input("多张源卡将合并到一个目标订阅。输入 merge 确认：") == "merge"
        )
    return validate_map(result)


def wizard():
    print("鸿蒙 → Android 本地迁移向导；不会自动写入手机记录。")
    source = Path(input("鸿蒙备份时间目录：").strip()).expanduser()
    print(json.dumps(inspect_backup(source), ensure_ascii=False, indent=2))
    kind = (
        input(
            "导入方式：1 自写安装器（可在手机选文件/SIM），2 SMS Backup & Restore，3 按目标备份样本识别 [1]："
        ).strip()
        or "1"
    )
    output_format = {"1": "native", "2": "sms-backup-restore", "3": "target"}.get(kind)
    if output_format is None:
        fail("invalid_choice", "无效选择。")
    samples = []
    profile = None
    if kind == "3":
        for label in ("目标短信备份（可留空）", "目标通话备份（可留空）"):
            value = input(label + "：").strip()
            if value:
                samples.append(Path(value).expanduser())
        profile = analyze_samples(samples)
        print(json.dumps(public_summary(profile), ensure_ascii=False, indent=2))
    choice = (
        input(
            "SIM 来源：1 安装器内选择，2 读取现有映射 JSON，3 USB 读取，4 使用目标样本，5 明确不保留 [1]："
        ).strip()
        or "1"
    )
    mapping, unassigned = None, choice == "5"
    if choice == "2":
        mapping = read_json(Path(input("映射 JSON：")).expanduser())
    elif choice in ("3", "4"):
        if choice == "3":
            adb = input("adb 路径 [adb]：").strip() or "adb"
            serial = input("设备序列号（仅一台设备时可留空）：").strip() or None
            apk = input("安装器 APK 路径（推荐；留空仅诊断文本读取）：").strip()
            bridge = Adb(adb, serial)
            print("如安装器已打开，请在手机授权读取 SIM 信息。")
            profile = bridge.probe(apk) if apk else bridge.discover()
        if profile is None:
            fail("missing_target_sample", "没有目标样本，请先运行 target 命令。")
        slots = [
            s.strip() for s in input("源卡槽（例如 0,1；单卡可只填 0）：").split(",")
        ]
        mapping = interactive_map(profile, slots)
    elif choice not in ("1", "5"):
        fail("invalid_choice", "无效选择。")
    actual_format = (
        profile.get("output_format") if output_format == "target" else output_format
    )
    if actual_format == "sms-backup-restore" and mapping is None and not unassigned:
        fail("missing_sim_mapping", "XML 路径需映射文件，或明确选择不保留卡归属。")
    output = Path(input("新输出目录：").strip()).expanduser()
    partial = (
        input("存在不支持记录时是否允许部分导出？输入 yes 才允许 [no]：").strip()
        == "yes"
    )
    report = convert(
        source,
        output,
        output_format=output_format,
        mapping=mapping,
        unassigned=unassigned,
        allow_partial=partial,
        target_samples=samples,
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "counts": report["counts"],
                "status": report["status"],
            },
            ensure_ascii=False,
        )
    )
    print(
        "转换完成。自写安装器可手动选择 migration.json，也可用 deliver 命令通过 USB 传入；实际写入必须在手机上确认。"
    )


def parser():
    p = argparse.ArgumentParser(
        prog="hmos-migrate",
        description="鸿蒙备份转换、目标样本识别与 Android 双卡映射。所有转换本地执行。",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("wizard", help="交互向导")
    inspect = sub.add_parser("inspect", help="只查看备份格式与模块")
    inspect.add_argument("backup", type=Path)
    target = sub.add_parser("target", help="识别目标短信/通话备份；二选一或两者")
    target.add_argument("--sms", type=Path)
    target.add_argument("--calls", type=Path)
    target.add_argument("--out", type=Path, required=True)
    discover = sub.add_parser(
        "discover", help="USB 读取 SIM 信息；--apk 使用公开 Android API"
    )
    discover.add_argument("--out", type=Path, required=True)
    discover.add_argument("--apk", type=Path)
    mapper = sub.add_parser("map", help="将源槽位与目标候选手动对应")
    mapper.add_argument("--profile", type=Path, required=True)
    mapper.add_argument("--slots", required=True)
    mapper.add_argument("--out", type=Path, required=True)
    converter = sub.add_parser("convert", help="转换鸿蒙加密备份")
    converter.add_argument("backup", type=Path)
    converter.add_argument("--out", type=Path, required=True)
    converter.add_argument(
        "--format", choices=("native", "sms-backup-restore", "target"), default="native"
    )
    converter.add_argument("--include", help="sms,calls,mms，留空选择备份中可用类别")
    converter.add_argument("--sim-map", type=Path)
    converter.add_argument("--unassigned", action="store_true")
    converter.add_argument("--target-sms", type=Path)
    converter.add_argument("--target-calls", type=Path)
    converter.add_argument("--allow-partial", action="store_true")
    deliver = sub.add_parser(
        "deliver", help="USB 安装自写 APK 并传入文件；不自动提交导入"
    )
    deliver.add_argument("--apk", type=Path, required=True)
    deliver.add_argument("--input", type=Path, required=True)
    for item in (discover, deliver):
        item.add_argument("--adb", default="adb")
        item.add_argument("--serial")
    return p


def main(argv=None):
    private_umask()
    args = parser().parse_args(argv)
    try:
        if args.command == "wizard":
            wizard()
            return 0
        if args.command == "inspect":
            result = inspect_backup(args.backup)
        elif args.command == "target":
            result = analyze_samples([p for p in (args.sms, args.calls) if p])
            write_json(args.out, result)
            result = public_summary(result)
        elif args.command == "discover":
            bridge = Adb(args.adb, args.serial)
            if args.apk:
                print(
                    "请在手机上的本地安装器中允许读取 SIM 信息；此过程不申请短信角色、不修改通信记录。",
                    flush=True,
                )
            result = bridge.probe(args.apk) if args.apk else bridge.discover()
            write_json(args.out, result)
            result = public_summary(result)
        elif args.command == "map":
            result = interactive_map(read_json(args.profile), args.slots.split(","))
            write_json(args.out, result)
            result = {"saved": str(args.out), "source_slots": list(result["slots"])}
        elif args.command == "convert":
            result = convert(
                args.backup,
                args.out,
                output_format=args.format,
                categories=args.include.split(",") if args.include else None,
                mapping=read_json(args.sim_map) if args.sim_map else None,
                unassigned=args.unassigned,
                allow_partial=args.allow_partial,
                target_samples=[p for p in (args.target_sms, args.target_calls) if p],
            )
            result = {
                "output": str(args.out),
                "counts": result["counts"],
                "status": result["status"],
                "source_slots": result["source_slots"],
            }
        else:
            Adb(args.adb, args.serial).deliver(args.apk, args.input)
            result = {
                "delivered": True,
                "next": "在手机选择/核对 SIM，预检查，完成授权后确认导入。",
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MigrationError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError) as exc:
        print(
            json.dumps(
                {
                    "error": "invalid_input_or_io",
                    "message": "输入不完整或文件操作失败。请检查路径、格式和权限。",
                    "exception": type(exc).__name__,
                },
                ensure_ascii=False,
            )
        )
        return 2
