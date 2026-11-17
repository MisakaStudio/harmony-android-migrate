# 发布与签名维护

## 发布身份

版本唯一来源为 `src/hmos_migrate/__init__.py`：`__version__` 控制 Python/APK 版本名，`ANDROID_VERSION_CODE` 控制 Android 升级序号。后续公开版本必须递增两者。普通版/USB 版共用 `dev.hmos.migrate` 和同一证书；切换前完成批次并恢复短信角色。

开发构建的 `build/local-debug.jks` 可以重新生成，不能用于公开 Release。正式发布构建不会生成密钥，且必须匹配 `docs/RELEASE_CERTIFICATE.txt` 的公开指纹。密钥丢失会影响后续覆盖升级，需私密加密备份整个签名目录。

仅在创建一个新的发布身份时执行一次：

```sh
python tools/init_signing.py --out-dir /secure/hmos-signing
```

工具生成随机密码并保存于该私有目录，不在终端打印密码。`release.jks`、`password.txt` 均不得加入 Git/上传 Release。Windows 使用前需限制目录 ACL。现有项目后续发布应复用维护者保存的密钥；Fork 创建自己的身份，并更新公开指纹，不冒充原项目签名。

## 构建两种发行包

安装 JDK 17、SDK Platform 36、Build-Tools 36.0.0。将以下 `CERTIFICATE_SHA256` 替换为公开指纹，不是密码：

```sh
python tools/build_android.py --sdk /path/to/sdk --variant standard --signing release \
  --keystore /secure/hmos-signing/release.jks \
  --store-pass-file /secure/hmos-signing/password.txt \
  --expected-cert-sha256 CERTIFICATE_SHA256
python tools/build_android.py --sdk /path/to/sdk --variant usb --signing release \
  --keystore /secure/hmos-signing/release.jks \
  --store-pass-file /secure/hmos-signing/password.txt \
  --expected-cert-sha256 CERTIFICATE_SHA256
```

两版均为已签名发行包；USB 版仍开启调试能力用于 `run-as`，发行签名不意味着关闭 USB 功能。默认 key alias 为 `hmos-release`；密钥密码不同时可指定 `--key-pass-file`。密码只以文件引用交给签名工具，不放在命令行文本中。

## 验证与打包

```sh
python -m unittest discover -s tests -v
python tools/check_release.py
python tools/package_release.py \
  --standard dist/hmos-migrate-0.1.1-standard.apk \
  --usb dist/hmos-migrate-0.1.1-usb.apk \
  --signer /path/to/sdk/build-tools/36.0.0/lib/apksigner.jar \
  --out dist/release-0.1.1
```

打包器验证实际 APK 签名、两版的版本/调试属性、清单哈希，拒绝开发/测试包；产物包含两种 APK、源码 ZIP、`BUILD-INFO.json` 和 `SHA256SUMS.txt`。只发布这些产物，不发布 `build/`、解密目录、签名材料或真实测试数据。

源码提交前先按发布白名单暂存，再运行 `python tools/check_release.py --git-index`；它核对暂存文件集合及字节内容与检查过的源码一致。不要在含真实备份的上级工作目录运行 `git add .`。

## GitHub Release

1. 更新 CHANGELOG 与兼容范围，提交经过检查的源码，推送后等待全部 CI 成功。
2. 创建与源码一致的 `v0.1.1` 标签；后续版本替换命令中的版本号。
3. 创建 Release draft，将验证过的全部发行产物加入附件；Release 说明列出新增行为、验证范围和已知限制。
4. 首个 0.1.x 发布标记为 pre-release，人工核对附件和校验值后发布。
5. 从 GitHub 下载附件验证 SHA256 与公开证书；不要只检查本地副本。

常规 CI 不保存或使用发布密钥，其 `development-apks-not-for-release` 产物只用于开发验证。发布采用维护者本地签名；无需把签名密码或私钥交给 PR 工作流。CI 的 Action 固定到 commit SHA，由 Dependabot 提交升级建议。

参考：[Android 签名](https://developer.android.com/studio/publish/app-signing)、[apksigner](https://developer.android.com/tools/apksigner)、[GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。
