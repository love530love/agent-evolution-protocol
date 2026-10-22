# Windows 便携 Python 核心

## 为什么分层

归属“红皇后—鲶鱼—创造性破坏”：环境也必须可复现、可诊断，不能靠每个 Agent 猜系统 Python。核心当前无第三方 Python 依赖，因此先提供官方嵌入式解释器的最小运行通道；开发及可选 MCP SDK 仍走独立虚拟环境。不将 embeddable 包改造成任意 pip 安装环境。

这是对通用便携环境路线的第一阶段，不是全部依赖已自包含。仍需 Windows x64、PowerShell 7 及可用 Windows 系统运行库；浏览器、.NET、WSL、模型和桌面 MCP 不在核心包里。

## 安装与启动

```powershell
# 预览：不下载、不建目录、不修改系统
./scripts/install-portable-python.ps1
# 在线安装：无需系统 Python，不修改 PATH/注册表，不请求管理员权限
./scripts/install-portable-python.ps1 -Install
# 离线：预先取得 manifest 对应的官方 ZIP，同样检查固定 SHA256
./scripts/install-portable-python.ps1 -Install -Archive 'D:/cache/python-3.13.12-embed-amd64.zip'
./aep.ps1 --runtime-doctor
./aep.ps1 --help
./aep.ps1 --coord-root .tools/portable-acceptance coord-improvement self-test
```

运行时位于 `.tools/portable-python/python-3.13.12-win-amd64`。默认脚本仅预览；显式 `-Install` 才下载或读取离线 ZIP，校验后解包到唯一 staging，记录文件哈希，禁止覆盖已有目标。失败保留 staging 证据，目录移动拒绝覆盖并发安装的胜出者。安装成功重复执行会检查完整性，不静默重装。首次下载有 120 秒超时，不自动重试。

统一入口 `aep.ps1` 使用固定相对路径、`-I -B` 和受控 `_pth`；忽略 PYTHONPATH/用户 site，不自动回退系统 Python，不导入 site，不自动安装插件。所有 CLI 参数原样转发，工作目录仍为调用者当前目录。启动前逐文件核验，缺失、变动及额外内容均拒绝。`--runtime-doctor` 只读完整性检查，不运行解释器；VERIFIED 不代表已验证所有业务功能。

## 迁移和更新

迁移可复制仓库源码、入口脚本、runtime manifest 及对应 `.tools/portable-python/<版本目录>`，保持相对布局；私有日志、信箱与证据按用户需要另行选择，禁止默认打包或上传。无需复制 `.venv`，禁止把绝对路径的 venv 当便携环境。源码更新仍由 Git 管理，运行时清单不冻结全部源码。

版本由 `runtime/python-runtime.json` 固定，当前 3.13.12 是已验收基线，不宣称最新安全版本。SHA256 来自 python.org 发布的 Sigstore bundle 内 messageDigest，与实际下载 ZIP 对比一致；本安装器不执行完整 Sigstore 身份/透明日志验证，不把哈希校验说成签名验真。清单和回执可被同机写权限者修改，不构成抵抗恶意篡改的安全边界。

新版本必须更新清单、单独安装、回归验收后再提交切换；旧目录保留便于随代码清单回退。尚无自动升级/自动回滚服务。不要手工往核心解释器注入第三方依赖，MCP 继续使用 `scripts/bootstrap-desktop.ps1` 配置的完整 Python/venv。

## 2026-10-22 验收与待开发

已在本机真实安装官方 ZIP，便携 CLI help、运行时诊断及 `coord-improvement self-test` 通过，后者含真实测试子进程，但复核角色仍为脚本，不代表多 Agent ACK。测试覆盖无副作用预览、缺环境、文件变动/额外文件拒绝、坏离线包拒绝。

另已复制源码、脚本、清单和运行时至带空格的新目录，重新执行完整性诊断及改进流水线自验收通过；这是同机路径迁移，不替代干净 Windows 机器验收。Windows Python 3.12 CI 作业增加实际下载固定 ZIP、便携诊断及工作流验收，远程运行结果须另核对。

可选 MCP 的 Windows CPython 3.13 完整依赖锁与离线 wheelhouse 已加入，见 [离线 SDK](OFFLINE_DESKTOP_SDK.zh-CN.md)，不污染便携核心。下一阶段：干净机器搬迁验收、带许可证及校验清单的发布包、完整 Python 离线引导、运行时安全更新策略、ARM64/Linux 配置。当前不提供“全组件离线一键可用”的承诺。

参考：[Python 官方 Windows 嵌入式说明](https://docs.python.org/3.13/using/windows.html#windows-embeddable)、[固定 ZIP 来源](https://www.python.org/ftp/python/3.13.12/)、[固定摘要来源](https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip.sigstore)。
