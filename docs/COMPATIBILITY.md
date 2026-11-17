# 兼容性与能力边界

兼容性分为源备份解析、目标数据字段和实际界面显示三层。不能由“APK 能安装”或“数据库存在 sub_id”推导整个迁移已验证。

## 源备份

| 格式 | 状态 |
|---|---|
| 原生鸿蒙 JSON 元数据、encryptionType=2、已知密钥封装 | 已实现 |
| `.info.json` / `info.json`、加密 manage.json、多个 part.N.tar | 已实现并以合成备份测试 |
| SQLite 主库与 WAL 分卷存放 | 已实现；清单缺卷会失败，不按空数据处理 |
| 旧 EMUI/Android 鸿蒙的 info.xml | 未实现 |
| 未知密钥封装、表结构或仅有云端备份 | 未实现，不猜测解密参数/字段 |

已知实际源格式样本来自 Pura X Max 的备份应用 7.0.0.550。系统名称或版本并不是格式承诺；匹配同一格式的其他设备可以尝试，失败时保留安全错误码和脱敏结构。

普通已收/已发短信与已知通话类型可转换。草稿、失败/排队短信、RCS、未知通话状态和未适配 MMS 不作为已成功迁移数据。图片 MMS 支持完整接收的 JPEG/PNG，重建基本 SMIL 图片布局，不保留任意动画、文字混排或其他媒体版式。

迁移范围是通信记录的核心字段与可验证的 SIM 归属，不包括通讯录、通话录音、厂商专有的高清/卫星/跨设备标记等所有系统数据。界面中的联系人名称通常依赖目标机通讯录。

电脑端当前限制：单个加密文件最多 512 MiB、数据库/归档成员通常最多 128 MiB、单张支持图片最多 20 MiB、目标格式样本最多 64 MiB。超限会明确停止，不把截断结果发布为成功。

## Android 目标

| 路径 | 设计范围 | 验证含义 |
|---|---|---|
| 标准 Android 公开 API 安装器 | Android 12/API 31 及以上，设备主用户 | 使用 RoleManager、SubscriptionManager、TelephonyManager、TelecomManager、ContentResolver |
| ADB 诊断文本识别 | 包含已实现的 AOSP 8/10–13/14+ 样式 | 只读取候选；OEM 文本差异可能返回 partial |
| 公共 API USB 探测 | Android 12+ | 由自写 APK 读取，不推导账户 ID 等于 subId |
| SMS Backup & Restore XML | 格式与官方字段规范一致 | 实际导入、去重与 SIM 标签由该应用及 ROM 决定 |
| 厂商通话 `simid` | 可选观察型扩展 | 只有目标机样本一致显示账户与字段关系，才带入映射 |

Android 12/API 31 与 Android 16/API 36 的隔离 AOSP 模拟器可运行仓库中的 Provider 集成测试。它们不能证明三星、小米、vivo、荣耀、OPPO 等全部 ROM 的 UI 行为。厂商机型应单独登记证据，不将品牌名作为通用主流程的条件。

OPPO/Android 16 的原型路径验证了 `simid` 与通话列表标签相关；这一事实不推广成“所有 OPPO 必须 simid=subId”，更不推广到其他品牌。标准字段始终是电话账户的 ID 与组件。

## SIM 身份

- 来源记录的 `source_slot` 是源库逻辑槽。历史换卡、eSIM 多端口等情况下，它不证明一个永久的实体 SIM 身份。
- 目标 `sub_id` 是短信订阅 ID。
- 通话列 `subscription_id` 的历史命名实际表示 PhoneAccount ID，是字符串，可能不是数字。
- PhoneAccount 的身份包含组件、ID 和用户。当前工具拒绝工作资料/次要用户账户。
- 同数字不是关联证据。自动关联只接受 Android 公开 API 返回的关系；样本候选必须人工核对。

## 权限与导入窗口

探测只申请读取电话状态。`TelephonyManager.getSubscriptionId(PhoneAccountHandle)` 能用该权限查询关联；为避免多余读取，工具不依赖需要号码权限的 `getPhoneAccount()` 标签接口，也不读取完整电话号码或 ICCID API。

部分账户 ID 本身可能是 ICCID，系统标签也可能包含用户填写的号码，因此目标 profile 是私有操作文件，分享前仍需脱敏。

安装器不是完整的日常短信客户端。短信/MMS 导入要求开启飞行模式并关闭 Wi-Fi，临时默认期间如果仍到达新消息，接收器会保留可获取的信息并提示处理；新彩信通知不等于已经下载附件。存在待处理记录时应导出报告并恢复常用短信应用，不能把它们标为无损完成。

通话读写属于受限制权限，某些安装来源或 ROM 会拒绝授权。工具不会自动关闭权限监控、root 或修改系统 AppOps。应改用合适安装来源、仅迁移可授权类别，或选择其他恢复路径。

## 完成标准

1. 目标原有记录仍在，未被清空或主动覆盖。
2. 源记录与写入/已有记录的数量、多重性和主要字段对应。
3. SIM 与电话账户字段正确；需保留卡标签时，在实际界面抽查。
4. 新写入彩信的地址、图片哈希、SMIL 和父记录均通过检查。
5. 未恢复项明确列出；原默认短信应用已恢复或用户明确确认替代默认应用。

系统可能重建索引、缓存及内部修改时间；这些不等于通话发生时间变化。

目标快照是用于核验和人工恢复的数据导出，不是厂商原生备份包。当前版本没有一键全库回滚功能；程序采用追加策略，未知或已被用户修改的记录不会被自动覆盖。

## 主要依据

- [Android Telephony / 默认短信角色](https://developer.android.com/reference/android/provider/Telephony)
- [TelephonyManager 账户到订阅关联](https://developer.android.com/reference/android/telephony/TelephonyManager#getSubscriptionId(android.telecom.PhoneAccountHandle))
- [Android CallLog 标准字段](https://developer.android.com/reference/android/provider/CallLog.Calls)
- [eSIM 多启用配置与逻辑槽](https://source.android.com/docs/core/connect/esim-mep)
