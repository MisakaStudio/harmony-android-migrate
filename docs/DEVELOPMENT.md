# 开发与验证

## Python

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python tools/check_release.py
```

测试覆盖认证失败、清单缺失、非隐藏元数据、多分卷与 WAL、仅短信/仅通话、部分导出阻止、目标 XML/ZIP、单类别样本、SIM 与电话账户分离、多设备选择及输出覆盖保护。

## Android 构建

安装 JDK 17+ 和官方 SDK：

```sh
sdkmanager "platforms;android-36" "build-tools;36.0.0" "platform-tools"
python tools/build_android.py --sdk /path/to/android-sdk
```

脚本不自行下载 SDK。默认普通开发版不开放调试通道，加 `--variant usb` 构建 USB 开发版。两者的开发签名位于 `build/local-debug.jks`，不能提交或用于公开发行。发行构建的外部密钥与证书校验见[发布维护](RELEASING.md)。对已安装的不同签名版本，安装可能失败；先导出其私有备份，不要自动清除数据。

## 隔离模拟器测试

只使用专门创建的合成测试 AVD，不使用个人 AVD 或真实手机。推荐分别测试 API 31 与 36，并使用名称 `HMOS_REPO_TEST_31`、`HMOS_REPO_TEST_36`。

```sh
python tools/build_android.py --sdk /path/to/android-sdk --variant usb --instrumentation --out build/hmos-migrate-test.apk
python tools/emulator_smoke.py --adb adb --serial emulator-5554 \
  --expected-avd HMOS_REPO_TEST_36 --apk build/hmos-migrate-test.apk
```

脚本检查 emulator 序列号、ranchu 硬件、AVD 名称、启动状态和主用户，拒绝真实手机。它安装测试 APK、临时改变**测试模拟器**的短信角色和无线状态，写入合成通信记录；结束时恢复测试前的短信角色。测试 AVD 保留合成记录，适合检查 UI。

覆盖：公开 API SIM 探测、Provider 写入与重跑、多重性、BOM 差异、同秒同图片但不同身份的彩信、MMS 父记录写入后的中断、SMIL 缺失恢复、日志映射绑定、逐记录日志、状态并发读写、界面重建与冻结映射。

测试版带 `SmokeTests` instrumentation；正常构建不包含它。不得把测试 APK 作为用户发布包。

## 发布前

- Python 单测与 APK 构建通过，模拟器结果保留在 `build/smoke-api-*.json`。
- 手动检查选文件、USB 传输、SIM 选择、屏幕重建、备份导出和系统默认短信恢复。
- 对实体设备只报告实际验证范围；增加品牌矩阵不能代替真实验证。
- 运行源包检查，确认没有私有数据、用户路径或签名密钥。

GitHub Actions 配置负责 Python 跨平台测试、普通/USB 开发包构建，以及 API 31/36 隔离 x86_64 模拟器集成测试；不会自动发布或向用户设备部署。工作流是否在远端通过，应以实际 Actions 结果为准。

## 0.1.1 本地发布验证（2026-11-17）

- 38 项 Python 测试通过，含版本一致性、发行包类型、证书匹配及私钥目录边界。
- API 36 隔离模拟器的 10 项迁移集成测试通过。
- 两种发行 APK 使用同一实际证书完成签名校验；普通版拒绝 USB Intent 与 `run-as`，USB 版探测和传入成功。
- 普通版/USB 版相互覆盖安装后，合成私有标记和迁移草稿保留，仍需确认映射，默认短信角色未改变。
- 图文指南截图来自隔离模拟器，没有真实通信数据。

## 0.1.0 本地验证记录（2026-11-17）

| 检查 | 结果与范围 |
|---|---|
| Python 单测 | 33 项通过；包括短信单样本、通话单样本和双样本的实际转换、JSON 结构/ID 校验，输出不包含目标样本原有通信记录 |
| Android 12 / API 31 | 隔离 AOSP ARM64 模拟器，10 项集成测试通过 |
| Android 16 / API 36 | 隔离 AOSP ARM64 模拟器，10 项集成测试通过 |
| 手动选文件 | API 36 系统文件选择器成功载入，保存草稿，仍需确认 SIM 映射 |
| USB | API 31/36 公共 API 探测成功；API 36 正常 APK 传入成功，未改变默认短信角色 |
| 构建与源码 | JDK 17 / API 36 编译及 APK 签名验证通过；源码产物与私有路径检查通过 |

测试只使用合成数据。模拟器通常只有一个可用 SIM；双卡/多槽映射另有合成回归测试，不等于所有实体双卡机型已实测。Windows/Linux 的运行矩阵已配置到 CI，本地记录不代表远端 CI 已执行。第三方恢复应用的实际导入行为和其他厂商 ROM 的通话标签仍需分别验证。
