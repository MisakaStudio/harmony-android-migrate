# Harmony Android Migrate

将原生鸿蒙的本地备份转换为 Android 可恢复的短信、通话记录和受支持的图片彩信。电脑端本地解密，提供两条导入路径：

- **SMS Backup & Restore**：生成该应用的 XML 备份，由用户手动恢复。
- **自写 Android 安装器**：手动选文件，或经 USB 传入；在手机上核对 SIM、备份、预检查后确认导入。

默认使用 Android 标准接口，不按品牌猜测卡号。OPPO 等厂商的额外通话标签字段仅在目标记录提供证据时使用。**“格式支持”不代表所有机型都已实测。** 当前版本为 `0.1.1` 预览版，具体范围见[兼容性](docs/COMPATIBILITY.md)。

[下载预览版](https://github.com/MisakaStudio/harmony-android-migrate/releases) · [图文指南](docs/USER_GUIDE.md) · [常见问题](docs/TROUBLESHOOTING.md) · [隐私说明](PRIVACY.md) · [更新记录](CHANGELOG.md)

| 安装包 | 用途 |
|---|---|
| `hmos-migrate-0.1.1-standard.apk` | 普通版，关闭调试模式，通过系统文件选择器导入 |
| `hmos-migrate-0.1.1-usb.apk` | USB 版，支持电脑探测/传入，也支持手动选文件；允许已授权 ADB 访问应用私有数据 |

两版同包名、同发布签名，不能同时安装。结束批次并恢复默认短信应用后，可以用同版本或更高版本覆盖切换。开发版与发布版签名不同，不能直接覆盖；不要通过随意卸载解决签名错误。发布包附 SHA256 校验清单和[公开证书指纹](docs/RELEASE_CERTIFICATE.txt)。

## 能做什么

- 读取已知 JSON 型鸿蒙加密备份，认证解密后才解析数据；支持 `.info.json`/`info.json`、多分卷和 SQLite WAL。
- 分别迁移短信、通话，或两者一起；源备份未选中的类别不必存在。
- 手动填写 SIM 映射、USB 读取目标 ID，或在安装器内选择。
- USB 公共 API 探测只读取目标信息，不申请默认短信角色、不导入数据。
- **目标格式兜底**：提供目标 Android 的短信备份、通话备份，任选一种或同时提供。自动识别已支持格式，读取账户候选，再生成对应格式的新文件。
- 默认阻止部分转换；缺附件、RCS、未知状态等会形成报告。只有明确允许，才导出已支持部分。
- 自写安装器会先备份、预检查，保留源内重复记录的数量；支持可识别的中断续传，不清空原有记录。

## 快速开始

需要 Python 3.10+。先下载源码包并解压，或克隆此仓库，然后在仓库根目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/hmos-migrate wizard
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\hmos-migrate.exe wizard
```

向导会询问鸿蒙备份目录、导入方式和 SIM 信息来源。备份目录应是**包含 `.info.json` 或 `info.json` 以及 `files` 的那一级时间目录**。密码隐藏输入，不放在命令行参数、配置或日志中。

### 路径 A：手动使用自写安装器

先生成文件，不需要目标手机连接电脑：

```sh
hmos-migrate inspect "/path/to/HMOSBackup/session"
hmos-migrate convert "/path/to/HMOSBackup/session" --out ./converted --format native
```

在 Android 安装普通版 `hmos-migrate-0.1.1-standard.apk`，将 `converted/migration.json` 复制到手机，然后：

1. 在安装器中选择迁移文件。
2. 读取目标 SIM，逐项核对来源卡槽与目标订阅/通话账户；也可以明确选择不保留卡归属。
3. 涉及短信/彩信时，开启飞行模式并关闭 Wi-Fi，再授予临时短信角色及所需权限。
4. 点击备份与预检查，核对新增、已有、冲突和续传数量，导出备份后确认写入。
5. 查看结果，恢复原默认短信应用，再恢复网络。保留备份；不要在未处理的新到消息/通知仍存在时卸载。

安装器只用于短时迁移，不是日常短信客户端。它不联网、不发送短信、不拨号。文件选择器若选择云盘，相关同步由用户选择的系统文档提供方负责。

### 路径 B：USB 读取 ID 并传入安装器

安装 Android SDK Platform-Tools，打开目标手机 USB 调试并授权。多设备时必须指定 `--serial`。

```sh
hmos-migrate discover --serial DEVICE_SERIAL --apk ./hmos-migrate-0.1.1-usb.apk --out ./target-profile.local.json
hmos-migrate map --profile ./target-profile.local.json --slots 0,1 --out ./sim-map.local.json
hmos-migrate convert "/path/to/HMOSBackup/session" --out ./converted --format native --sim-map ./sim-map.local.json
hmos-migrate deliver --serial DEVICE_SERIAL --apk ./hmos-migrate-0.1.1-usb.apk --input ./converted/migration.json
```

`discover --apk` 会安装自写探测/导入应用，手机上仍需允许读取电话状态。`deliver` 校验传输文件后打开安装器，**不会自动确认写入**。

不提供 `--apk` 时，只解析 ADB 诊断文本；结果可能不完整，不会将数字相同的电话账户与短信订阅自动关联。

### 路径 C：生成 SMS Backup & Restore 文件

```sh
hmos-migrate convert "/path/to/HMOSBackup/session" --out ./xml-backup \
  --format sms-backup-restore --sim-map ./sim-map.local.json
```

输出 `messages.xml`、`calls.xml`（只选择的类别才生成）及 `report.json`。复制到 Android，再使用 [SMS Backup & Restore](https://www.synctech.com.au/sms-backup-restore/) 的本地恢复功能。实际 SIM 标签效果依赖其版本和目标 ROM，恢复后需抽查。

若明确不需要卡归属，可使用 `--unassigned`。默认不会静默丢弃 SIM 映射。

## USB 无法识别时：导入目标备份样本

三种情况都支持：

```sh
# 只有短信样本
hmos-migrate target --sms target-messages.xml --out target-profile.local.json

# 只有通话样本
hmos-migrate target --calls target-calls.xml --out target-profile.local.json

# 同时提供两种样本
hmos-migrate target --sms target-messages.xml --calls target-calls.xml --out target-profile.local.json
```

然后手动对应源卡槽：

```sh
hmos-migrate map --profile target-profile.local.json --slots 0,1 --out sim-map.local.json
hmos-migrate convert "/path/to/HMOSBackup/session" --out ./target-format \
  --format target --target-sms target-messages.xml --target-calls target-calls.xml \
  --sim-map sim-map.local.json
```

只拥有一种样本时，删除另一条 `--target-*` 参数即可。只迁移一种类别时加 `--include sms` 或 `--include calls`。

**格式识别与账户识别是两件事。** 一份通话 XML 足以识别 XML 格式家族，但不能证明短信订阅 ID。只提供通话样本时，可在映射向导输入 `c` 仅映射通话；缺少另一类别 ID 时，使用额外样本、USB/安装器读取、手工配置，或明确不保留归属。样本内的原有消息不会被复制到迁移文件中。

当前支持：SyncTech 明文 XML、未加密 XML ZIP，以及本工具的原生 JSON/备份 JSON。未知 XML、厂商私有/加密备份不会被猜测成可写格式。详见[目标格式](docs/FORMATS.md)。

## 范围与限制

- 源格式按文件结构识别，不把鸿蒙系统版本号当作保证。旧 Android/EMUI 的 `info.xml` 备份未适配。
- 自写 APK 安装下限为 Android 12/API 31；标准路径使用公开 API，主用户模式，不要求 root。厂商权限策略仍可能拒绝通话记录访问。
- 当前图片彩信支持完整接收的 JPEG/PNG；不完整附件、文字/其他媒体部件、RCS 和未支持状态默认阻止导出。
- 安装器文件上限 32 MiB、单批最多 50,000 条、解码图片总计 20 MiB；超限明确停止。较大数据可选择类别分批，或使用 XML 路径。
- 兼容通话时间缺失时，只能使用源库创建时间并在报告标注。Android 彩信日期按秒保存。
- 不承诺跨 Provider 的全库事务。已提交记录保留，日志记录逐条状态；未知/非本工具所有的残缺彩信不会被擅自覆盖。

## 构建与测试

APK 构建需要 JDK 17+、Android SDK Platform 36 和 Build-Tools 36.0.0：

```sh
python tools/build_android.py --sdk /path/to/android-sdk
```

默认生成 `dist/hmos-migrate-dev-standard.apk`；加 `--variant usb` 生成开发 USB 版。开发签名保存在被忽略的 `build/`，不用于公开发行。发布构建必须显式提供仓库外的签名材料和证书指纹，详见[发布维护](docs/RELEASING.md)。USB 私有通道依赖 `run-as`，不能直接关闭 USB 版的调试标志。

```sh
python -m unittest discover -s tests -v
```

安装包集成测试只允许在明确命名的隔离模拟器中使用合成数据，详见[开发与测试](docs/DEVELOPMENT.md)。

## 目录与贡献

```text
src/hmos_migrate/       备份解析、目标格式、SIM 映射、ADB、CLI
android/               自写安装器与模拟器测试
tests/                 纯合成备份与回归测试
examples/              不含真实数据的配置/格式样本
tools/                 构建、发布检查、模拟器测试脚本
docs/                  格式、兼容性与故障处理
```

新增厂商或备份格式前，请提供脱敏结构和可复现的合成测试，不上传真实短信、号码、ICCID、密码、签名私钥或备份数据库。[架构与适配说明](docs/ARCHITECTURE.md)列出了扩展边界。

许可证：MIT。仓库仅提供本地工具，不包含用户通信数据。[参与贡献](CONTRIBUTING.md) · [私密报告安全问题](SECURITY.md)
