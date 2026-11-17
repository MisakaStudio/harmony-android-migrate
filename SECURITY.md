# 安全问题报告

当前维护范围为最新的 0.1.x 预览版本；没有承诺固定响应时限。

通过 GitHub 的 [Report a vulnerability](https://github.com/MisakaStudio/harmony-android-migrate/security/advisories/new) 私密提交漏洞。提供受影响版本、复现条件、影响和合成样本。不要公开发布认证绕过、任意写入等未修复细节，也不要提交真实通信备份、密码或私钥。若私密入口不可用，可发不含细节的 Issue 请求维护者启用入口。

## 边界

- 电脑端本地解密与转换，Android 应用不请求 INTERNET 权限。
- USB 版允许已授权 ADB 的 `run-as` 私有目录访问，适合短期迁移；普通版关闭调试入口。
- 安装器临时取得短信角色，必须恢复常用短信应用；并非日常短信客户端。
- 目标备份/日志和 SIM profile 是私有数据，不能直接当作可公开诊断报告。
- 数据追加不等于跨 Provider 事务；中断时保留日志，不能承诺全库自动回滚。

验证下载文件的 SHA256，并核对 [发布证书](docs/RELEASE_CERTIFICATE.txt)。签名材料不存放于 Git 仓库，PR/常规 CI 不接触发布密钥。普通版与 USB 版使用同一身份，开发版签名不保证与发行版兼容。
