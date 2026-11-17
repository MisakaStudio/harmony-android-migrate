# 文件格式与目标样本识别

## 目标样本

`target` 命令读取文件内容，不依据扩展名猜测。只取格式、类别、SIM/账户候选及一致的已知扩展字段，不把目标样本中的通信记录合并进输出。

| 识别格式 | 结构 | 可生成输出 |
|---|---|---|
| SyncTech SMS/MMS XML | `<smses count="...">`，`<sms>` / `<mms>` | `messages.xml` |
| SyncTech Call XML | `<calls count="...">`，`<call>` | `calls.xml` |
| 未加密 XML ZIP | 首个文件为上述 XML | 对应明文 XML |
| 本工具 native JSON | `schema=hmos-migrate/native, version=1` | 安装器 native JSON |
| 本工具 Provider 备份 JSON | `schema=hmos-migrate/provider-backup, version=1` | 配套安装器格式 |

本工具的报告 ZIP 在首个文件是 `before.json` 时也可作为样本。AES ZIP、厂商私有容器、任意 XML、VMSG/CSV、未知 JSON 均未适配。识别失败不生成“看起来像备份”的猜测文件。

目标样本可以是短信、通话，或两者。空样本能证明格式，不能证明账户。只有短信样本时不会补造电话账户；只有通话样本时不会把账户 ID 当成短信订阅 ID。

样本上限 64 MiB；ZIP 会检查加密标记、成员数量和解压大小。XML 禁止 DTD/实体声明。运行输出默认隐藏长账户 ID，完整操作 profile 仅保存在本地。

JSON 样本也会校验已知类别、数组/记录结构和关键字段；仅有 schema 标签不能证明格式。短信订阅 ID 与厂商 `simid` 只接受整数或整数字符串，小数和布尔值会被拒绝。空类别必须明确写为空数组，未知类别不会被当作空备份。

## SIM map，版本 1

```json
{
  "schema": "hmos-migrate/sim-map",
  "version": 1,
  "allow_merge": false,
  "slots": {
    "0": {
      "sub_id": 101,
      "phone_account": {
        "id": "account-alpha",
        "component": "com.example.phone/com.example.phone.ConnectionService",
        "user_id": 0
      }
    }
  }
}
```

示例值不能直接用于真实设备。来源槽键可为 `-1`（未知）或任意实际非负槽位，不限定双卡。短信只需要 `sub_id`，通话只需要 `phone_account`；涉及两类时两者都要提供。

`{"unassigned": true}` 表示用户明确不保留该来源组的卡归属。多来源合并到同一订阅/电话账户需要 `allow_merge=true`。

可选 `call_extras: {"simid": 909}` 仅用于已有目标记录证实的厂商字段。组件缩写 `package/.Class` 会规范化为 `package/package.Class`。来源槽、目标订阅、电话账户、厂商标签字段是独立概念。

## Native migration，版本 1

根字段：

- `schema`: `hmos-migrate/native`
- `version`: `1`
- `date_units`: SMS/calls 为 `milliseconds`，MMS 为 `seconds`
- `conversion`: 完整性、未支持项、修复说明和计数
- `sim_map`: 目标建议映射，允许为 null，由安装器现场选择
- `records`: `sms`、`calls`、`mms` 三个数组
- `batch_id`: 转换数据标识；安装器还计算文件 SHA-256，防止中断后混用不同输入

每条记录含唯一的 `source_id`、`source_slot`、`values`。MMS 另有 `parts`（Base64 数据和 SHA-256）及 `addresses`。`values` 只允许已定义 Provider 字段，不允许外部传入 `_id`、`_data`、`creator`、任意文件路径等。

短信只导入收件箱/已发送状态，不生成待发送队列。MMS 只导入已接收图片类型。导入器没有网络权限，没有短信发送或拨号调用。

## XML 路径的时间

SyncTech XML 的 SMS、MMS、calls 日期均按毫秒表示；通话时长按秒。Android MMS Provider 按秒保存日期，只有 native 写入路径做毫秒到秒的转换，不能再次除以 1000。

参考：[SyncTech 官方 XML 字段](https://www.synctech.com.au/sms-backup-restore/fields-in-xml-backup-files/)。
