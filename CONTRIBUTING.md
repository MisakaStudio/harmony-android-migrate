# 参与开发

先阅读 [架构](docs/ARCHITECTURE.md)、[格式](docs/FORMATS.md)和[开发与测试](docs/DEVELOPMENT.md)。问题讨论和文档默认使用中文，代码标识符保持英文。

## 提交问题

使用 Issue 模板，提供机型、系统完整版本、备份应用版本、工具版本、导入路径、错误码及匿名数量。公开 Issue 不接收真实备份、电话号码、ICCID、短信正文、密码、签名文件或未脱敏截图。安全漏洞走 [私密报告](https://github.com/MisakaStudio/harmony-android-migrate/security/advisories/new)。

## 新格式与厂商适配

1. 先说明格式签名、字段含义、日期单位及证据来源，不以品牌名称猜测实现。
2. 用代码生成合成数据库/归档，复现失败与预期成功；不要提交真实数据后再打码。
3. 适配短信订阅与通话账户时保持独立语义，不按相同数字关联。
4. 覆盖缺失字段、损坏文件、空类别、双卡、多重性及中断；更新兼容矩阵。

运行 `python -m unittest discover -s tests -v` 和 `python tools/check_release.py`。Android 变化需在专用模拟器验证；不能对用户真实手机自动运行写入测试。

PR 描述问题与最终行为、测试证据和未验证范围。依赖和公共格式变更须说明必要性。保留外部代码的许可证与来源；项目贡献按仓库 MIT 许可证分发。
