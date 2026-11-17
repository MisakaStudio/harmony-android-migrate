package dev.hmos.migrate;

final class Messages {
  static String error(String code) {
    String text;
    switch (code) {
      case "mapping_confirmation_required":
        text = "请先核对并勾选 SIM 对应关系";
        break;
      case "select_target_subscription":
        text = "请选择目标短信订阅，或明确不保留卡归属";
        break;
      case "select_target_phone_account":
        text = "请选择目标通话账户；短信订阅 ID 不能替代电话账户";
        break;
      case "sim_merge_confirmation_required":
        text = "多张源卡将合并到同一个目标账户，需要明确确认";
        break;
      case "partial_source_not_confirmed":
        text = "源转换存在未包含记录，请先查看报告，再决定是否接受部分迁移";
        break;
      case "duplicate_conflict_confirmation_required":
        text = "发现跨卡或彩信身份不同的相似记录，请核对预检查结果";
        break;
      case "airplane_required":
        text = "请开启飞行模式，再继续同一批次";
        break;
      case "wifi_must_be_off":
        text = "请关闭 Wi-Fi，避免临时短信角色影响新到消息";
        break;
      case "sms_role_required":
        text = "需要先允许临时默认短信角色";
        break;
      case "restore_old_sms_role_first":
        text = "请先在系统设置恢复常用短信应用，再开始新批次";
        break;
      case "permission_denied":
        text = "系统拒绝了所需权限。请检查授权或选择其他恢复方式";
        break;
      case "target_subscription_changed":
        text = "目标 SIM 已变化，请重新核对映射";
        break;
      case "target_phone_account_changed":
        text = "目标电话账户已变化，请重新核对映射";
        break;
      case "subscription_account_mismatch":
        text = "短信订阅与通话账户不是同一张目标卡";
        break;
      case "vendor_field_unavailable":
        text = "目标手机不支持指定的厂商扩展字段，请修正映射";
        break;
      case "vendor_field_conflict":
        text = "厂商卡标签字段与目标机现有记录冲突";
        break;
      case "input_changed":
      case "mapping_changed":
        text = "迁移文件或映射已变化，不能混入原批次继续写入";
        break;
      case "completed_record_changed":
        text = "已完成的记录后来发生变化，工具不会覆盖它。请导出报告检查";
        break;
      case "existing_incomplete_mms_conflict":
        text = "已有彩信不完整，且无法确认属于当前批次，已停止以免误覆盖";
        break;
      case "uncertain_interrupted_insert":
        text = "中断后无法唯一确认已写入记录，已保留日志并停止";
        break;
      case "attachment_checksum_failed":
      case "attachment_verification_failed":
        text = "彩信附件校验失败，未将残缺数据标记为完成";
        break;
      case "unsupported_schema":
      case "invalid_date_units":
        text = "文件不是受支持的安装器迁移格式，请用配套转换器重新生成";
        break;
      case "file_too_large":
      case "package_limit_exceeded":
        text = "文件超过安装器处理上限，请减少类别或使用 XML 路径";
        break;
      case "unsupported_user":
        text = "仅支持设备主用户，不支持工作资料或次要用户";
        break;
      case "export_wait_for_import":
        text = "正在写入，请等待结束后再导出报告";
        break;
      case "file_or_storage_error":
        text = "文件或存储操作失败，请检查可用空间并保留现有备份";
        break;
      default:
        text = "数据格式或设备接口暂不能继续处理，请保留报告排查";
    }
    return text + "（" + code + "）";
  }

  static String kind(String value) {
    return value.equals("sms") ? "短信" : value.equals("calls") ? "通话" : "彩信";
  }
}
