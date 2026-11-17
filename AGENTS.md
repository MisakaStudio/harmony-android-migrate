# 项目约定

- Python 3.10+，只依赖 cryptography；Android 为 Java 8 语法、JDK 17+ 构建、minSdk 31 / targetSdk 36。
- 电脑端入口：`src/hmos_migrate/cli.py`；Android 源码：`android/app/src/main/java/dev/hmos/migrate/`。
- 保持 Python 输出与 Android `PackageData` 的 schema/version 和时间单位一致。
- 单测：`python -m unittest discover -s tests -v`；APK 构建：`python tools/build_android.py --sdk <SDK>`。
- 真实短信/通话、备份、ICCID、密码、私钥不能进入仓库或测试夹具。测试只能使用合成数据。
- 不在真实手机或个人 AVD 上自动运行集成测试。使用明确命名的隔离模拟器，并核验序列号、硬件和 AVD 名称。
- 不把未知格式当空数据成功，不把电话账户 ID 当短信订阅 ID，不按品牌默认补造私有字段。
- 不通过关闭系统权限保护、root 或自动更改 AppOps 来处理权限失败。
- 版本唯一来源为 `src/hmos_migrate/__init__.py`；普通版不调试，USB 版调试。发布构建必须复用仓库外密钥，并验证 `docs/RELEASE_CERTIFICATE.txt`。
- 未经明确请求，不提交、推送、发布或操作用户设备。
