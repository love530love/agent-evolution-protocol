# 可选 MCP SDK：冻结依赖与离线安装

## 定位与设计

便携核心解决“没有系统 Python 也能运行 AEP”；本模块解决“安装 MCP 时依赖飘移、断网重复配置”。两条通道隔离：不往最小嵌入式解释器强装 pip，不修改系统 Python，不把下载或 SDK 导入说成已经唤醒队友。

红皇后要求对外部依赖变化持续回归；鲶鱼优先检验缺包、损坏、平台错配和隐式联网；创造性破坏要求新锁单独验收后替换旧锁，不在生产环境自动升级。依赖冻结提供可复现性，不证明这些版本没有漏洞。

## 快速操作（PowerShell 7）

```powershell
# 只看计划，不联网，不创建目录，不执行包
./scripts/prepare-desktop-wheelhouse.ps1
# 有网机器预下载固定文件，保存 wheelhouse
./scripts/prepare-desktop-wheelhouse.ps1 -Action Download
# 将 wheelhouse 复制到离线机器后先核验
./scripts/prepare-desktop-wheelhouse.ps1 -Action Verify -Directory 'D:/offline/desktop-wheelhouse'
# 必须明确指定完整 Windows x64 Python 3.13（含 venv/ensurepip）
./scripts/bootstrap-desktop.ps1 -Python 'D:/Python313/python.exe' -Wheelhouse 'D:/offline/desktop-wheelhouse' -Install
./aep-desktop.ps1 --help
./aep-desktop.ps1 coord-desktop inventory
```

`runtime/desktop-win-cp313.lock.json` 固定 MCP 1.30.0 和全部解析到的 30 个依赖 wheel，含具体版本、文件名、SHA256 与 PyPI 文件 URL。锁由 Windows CPython 3.13 目标解析，下载文件摘要与 PyPI 对应版本 JSON 比对；不是密码学签名验真。当前锁仅支持 Windows x64 CPython 3.13，不是跨平台通用锁。

Download 只访问锁定的 pythonhosted.org HTTPS 文件，不实时解析“最新版本”。唯一 staging 目录完整下载并验哈希后才发布，禁止覆盖已有目录；已有完整目录只校验。失败保留 staging，不自动重试或删除。Verify 只读；缺包、变动、额外文件/目录、非法文件名和非预期来源均失败。

离线安装先验完整 wheelhouse 和解释器平台，再创建新 venv，生成精确版本及哈希 requirements，以 pip `--isolated --no-index --require-hashes --only-binary=:all:` 安装，随后 pip check 和 MCP/AEP 导入验收。缺依赖不会偷偷上网补齐；不构建 sdist，不从服务器执行构建脚本。没有 Wheelhouse 的旧在线安装入口仍保留，不能声称也已锁定。

成功后 `aep-desktop-install.json` 记录使用的锁摘要。它不是数字签名或整个安装目录的持续完整性证明；拥有本机文件写权限者仍能改动环境。导入受信任依赖本身会执行代码，此过程不是安全沙箱。

2026-10-22 本机证据：30 个文件的下载与哈希校验通过，新建 SDK 在无索引模式安装成功，pip check 无冲突，MCP、win32api、cryptography 导入和完整改进工作流自验收通过。自验收含真实测试子进程，复核仍是脚本角色，不是队友 ACK；没有通过断网防火墙测试证明所有进程无法联网。

## 启动和迁移边界

`aep-desktop.ps1` 固定使用 `.tools/desktop-sdk/Scripts/python.exe`，加载当前仓库 src，并显式导入 MCP；不自动安装、不回退便携核心或系统解释器。离线模式只安装 SDK，AEP 从当前源码运行，不另装一个可能过期的 AEP wheel；新 venv 内写入明确的 aep-repository.pth 源码路径，使旧工具的 `SDK-python -I -m agent_evolution_protocol` 入口仍可使用。源码链接只作用于该 venv，不修改系统 site。自定义 EnvironmentPath 时应明确使用对应解释器，不冒充默认环境已安装。

完整 Python 和 venv 尚未成为可搬迁便携发行物；迁移到新机器应复制锁、wheelhouse、源码并重新创建 venv，不直接复制旧 venv。浏览器、桌面 MCP 服务、.NET、WSL 仍需各自安装验收。SDK inventory 只是能力登记，不证明工具发现、原窗口投递或 ACK。

## 维护路线

更新依赖必须在新的隔离下载目录重新解析，核对 PyPI 摘要，更新锁并通过离线安装与业务回归，再替换默认锁；禁止安装时自动重解析或原地更新旧 SDK。[候选锁生成与审查](DEPENDENCY_CANDIDATES.zh-CN.md) 已提供；安装与准备脚本可用明确的 -LockFile 指向候选，既有默认锁不变。自动依赖解析、漏洞审计、第三方许可证材料汇总、完整 Python 离线引导、运行中 SDK 完整性检查和真正断网机器验收仍待完善。

参考：[pip download](https://pip.pypa.io/en/stable/cli/pip_download/)、[pip 哈希锁定安装](https://pip.pypa.io/en/stable/topics/secure-installs/)。
