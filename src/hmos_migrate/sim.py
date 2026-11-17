from copy import deepcopy
from .common import fail

MAP_SCHEMA = "hmos-migrate/sim-map"
PROFILE_SCHEMA = "hmos-migrate/target"
CALL_EXTRAS = {"simid"}


def component_name(value):
    if not isinstance(value, str) or value.count("/") != 1:
        fail("invalid_component", "电话账户组件必须是 package/class。")
    package, name = value.split("/", 1)
    if not package or not name or any(ord(c) < 32 for c in value):
        fail("invalid_component", "电话账户组件格式无效。")
    return package + "/" + (package + name if name.startswith(".") else name)


def validate_map(mapping):
    if mapping is None:
        return None
    mapping = deepcopy(mapping)
    if mapping.get("schema") != MAP_SCHEMA or mapping.get("version") != 1:
        fail("invalid_sim_map", "SIM 映射 schema/version 不受支持。")
    slots = mapping.get("slots")
    if not isinstance(slots, dict) or not slots:
        fail("invalid_sim_map", "SIM 映射至少需要一个源卡槽。")
    assigned, assigned_accounts = [], []
    for key, item in slots.items():
        if (
            not isinstance(key, str)
            or not key.lstrip("-").isdigit()
            or int(key) < -1
            or str(int(key)) != key
            or not isinstance(item, dict)
        ):
            fail("invalid_sim_map", "源卡槽必须是 -1（未知）或非负整数的字符串。")
        if item.get("unassigned") is True:
            if any(k in item for k in ("sub_id", "phone_account", "call_extras")):
                fail("invalid_sim_map", "未指定映射不能同时指定目标账户。")
            continue
        sub = item.get("sub_id")
        if sub is not None and (type(sub) is not int or sub < 0):
            fail("invalid_sim_map", "目标订阅 ID 必须是非负整数，不等于卡槽号。")
        if sub is not None:
            assigned.append(sub)
        account = item.get("phone_account")
        if sub is None and account is None:
            fail("invalid_sim_map", "映射至少需要短信订阅或电话账户之一。")
        if account is not None:
            if not isinstance(account, dict) or not all(
                isinstance(account.get(k), str) and account[k]
                for k in ("id", "component")
            ):
                fail("invalid_sim_map", "电话账户需要原样提供 id 和 component。")
            if (
                type(account.get("user_id", 0)) is not int
                or account.get("user_id", 0) != 0
            ):
                fail("unsupported_user", "当前安装器仅支持设备主用户，不支持工作资料。")
            if (
                "/" not in account["component"]
                or "[" in account["id"]
                or "*" in account["id"]
            ):
                fail("invalid_sim_map", "电话账户格式不正确或已被脱敏，不能用于恢复。")
            account["component"] = component_name(account["component"])
            assigned_accounts.append(
                (account["component"], account["id"], account.get("user_id", 0))
            )
        extras = item.get("call_extras", {})
        if not isinstance(extras, dict) or set(extras) - CALL_EXTRAS:
            fail("unsupported_call_extension", "未知厂商通话字段，需要明确适配。")
        if any(type(v) is not int or v < 0 for v in extras.values()):
            fail("invalid_sim_map", "厂商通话字段必须来自目标机的非负整数值。")
    if (
        len(assigned) != len(set(assigned))
        or len(assigned_accounts) != len(set(assigned_accounts))
    ) and not mapping.get("allow_merge", False):
        fail("merged_sim_map", "多张源卡映射到同一订阅时，需要显式 allow_merge=true。")
    return mapping


def apply_map(records, mapping, *, unassigned=False):
    mapping = validate_map(mapping)
    result = deepcopy(records)
    for kind, rows in result.items():
        for record in rows:
            slot = str(record["source_slot"])
            entry = mapping["slots"].get(slot) if mapping else None
            if entry is None:
                if not unassigned:
                    fail(
                        "missing_sim_mapping",
                        f"源卡槽 {slot} 缺少映射；可手工填写、USB 读取或在安装器中选择。",
                    )
                entry = {"unassigned": True}
            values = record["values"]
            if kind in ("sms", "mms"):
                if not entry.get("unassigned") and "sub_id" not in entry:
                    fail(
                        "missing_subscription",
                        f"源卡槽 {slot} 的短信需要订阅 ID；通话账户 ID 不能替代它。",
                    )
                values["sub_id"] = -1 if entry.get("unassigned") else entry["sub_id"]
            else:
                for key in (
                    "subscription_id",
                    "subscription_component_name",
                    *CALL_EXTRAS,
                ):
                    values.pop(key, None)
                if not entry.get("unassigned"):
                    account = entry.get("phone_account")
                    if account is None:
                        fail(
                            "missing_phone_account",
                            f"源卡槽 {slot} 的通话需要电话账户 ID/component；短信订阅 ID 不能替代它。",
                        )
                    values.update(
                        subscription_id=account["id"],
                        subscription_component_name=account["component"],
                    )
                    values.update(entry.get("call_extras", {}))
    return result


def map_from_profile(profile, choices, *, allow_merge=False):
    """choices maps source slot to an explicit subscription-list index."""
    if profile.get("schema") != PROFILE_SCHEMA or profile.get("version") != 1:
        fail("invalid_target_profile", "目标信息格式不受支持。")
    slots = {}
    for source, index in choices.items():
        try:
            index = int(index)
            if index < 0:
                fail("invalid_target_choice", "目标候选序号不能为负数。")
            sub = profile["subscriptions"][index]
        except (KeyError, IndexError, ValueError, TypeError):
            fail("invalid_target_choice", "目标订阅选择不存在。")
        entry = {"sub_id": sub["sub_id"]}
        accounts = [
            a
            for a in profile.get("phone_accounts", [])
            if a.get("sub_id") == sub["sub_id"]
            and a.get("association") in ("android_api", "manual")
            and a.get("id_state", "exact") == "exact"
        ]
        if len(accounts) == 1:
            account = accounts[0]
            entry["phone_account"] = {
                k: account[k] for k in ("id", "component", "user_id")
            }
            if account.get("call_extras"):
                entry["call_extras"] = account["call_extras"]
        slots[str(source)] = entry
    return validate_map(
        {"schema": MAP_SCHEMA, "version": 1, "allow_merge": allow_merge, "slots": slots}
    )
