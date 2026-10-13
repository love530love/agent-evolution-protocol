# 军团简报：REA 与 WSLC（2026-10-13）

事件：`REA-WSLC-20261013`。收到后请在原会话回复 `AEP-ACK:REA-WSLC-20261013`，并分别报告 REA CLI/MCP 与 WSLC 容器的**实测**状态。不要新建会话，不要把看到信箱当作已接单。

## REA：证据能力，不是自动提高模型智力

Windows PowerShell 7：`rea.cmd --version`，已安装版本为 6.4.0。Codex/Hermes 的 MCP 注册已修复，但各自客户端重连后才可验工具是否出现；WorkBuddy/Qoder 等先检查 CLI 可达，MCP 接入需独立验收。仅在**获授权目标**的二进制、发布制品、Electron/JavaScript、.NET 或运行行为问题上使用 REA；普通完整源码问题先读源码。先查已有同 hash/版本证据，区分【实】【源】【推】，保留 Evidence ID/路径、覆盖范围与未知项。不要自动跑 `rea update` 或每轮 `setup`。完整规则见 `K:\PythonProjects5\agent-evolution-protocol\docs\coordination\REA_CAPABILITY.md`。

## WSLC：它不是 WSL 发行版

**2026-10-13 21:40 后的新状态（优先于下文旧简报）：**已用 WSLC `save/load` 将昆仑镜像从管理员会话复制进普通会话；普通会话中镜像 ID `9b5a960d9cc3`、容器 `azure_ellsworth`（ID `e15083a2a78a`）已创建并启动，`wslc exec`、Python/Torch 导入通过。普通会话无需管理员权限即可访问这份**新副本**。`torch.xpu.is_available()` 为 `False`、设备数 0；这不证明物理 XPU 或完整编译门可用。原管理员会话和镜像未删除。请先用 `wslc images`、`wslc ls`、`wslc inspect azure_ellsworth` 核对当前会话；不得把旧文的“必须提权”套用到新副本，也不得在任一会话因空表而盲目 `run/pull`。详见 FlagGems 共享知识文档顶部状态。

程序为 `C:\Program Files\WSL\wslc.exe`；协作区 2026-10-13 通报昆仑容器名 `azure_ellsworth`，镜像约 36 GB。PowerShell 7 只读起步：

```powershell
$wslcExe = 'C:\Program Files\WSL\wslc.exe'
& $wslcExe ls
```

**更正（以协作区 16:4x 的复测为准）：必须在提权会话中访问现有昆仑镜像。** `wslc` 按 session 隔离存储，镜像在 `wslc-cli-admin-love` 的 `storage.vhdx`；非提权的 `wslc-cli-love` 中，`ls` 和 `images` 都是空表，`start azure_ellsworth` 报 `CONTAINER_NOT_FOUND`，显式选 admin session 报 `ERROR_ELEVATION_REQUIRED`。我之前写“`ls` 为空仅说明上下文变化”不够准确，遗漏了这条硬前提。不要在普通会话尝试 `run --rm`，否则可能重拉约 36 GB 镜像。

**授权更新（2026-10-13）：**用户允许 Agent 为获授权任务主动发起管理员 CMD / PowerShell 7，无需再等待用户亲自双击脚本。Windows UAC 仍须由系统完成批准；发起 `RunAs` 不等于已获管理员令牌，更不保证无人值守可执行。启动后应核实权限，再在**提权会话**先运行 `wslc images`，确认现有镜像的 repository 与 tag；缺失或命令报错即停止，绝不以 `run --rm` 触发重拉。现有 `K:\PythonProjects5\FlagGems-sglang\RUN_XPU_GATE_PKG.cmd` 有镜像保护，但也含交互式 `pause`，不能把它视为已验收的无人值守入口。若需持续无人值守，应一次性配置限定操作、可审计的提升权限任务，并验证普通 Agent 能触发且能读回结果；不得创建可接受任意命令的管理员通道。提权后且 `images` 确认目标镜像时，才可在该会话 `start/exec azure_ellsworth`。这仍只用于昆仑源码/编译门，不是 OCTO 服务栈。Git Bash 必须禁用 MSYS 路径转换，详见 `K:\PythonProjects5\FlagGems-sglang\competition\coordination\knowledge\WSLC_KUNLUN_CONTAINER.md` §0。

本次指令只要求**阅读、核验和 ACK**，不授权安装分析引擎、升级 REA、启动容器、提交竞赛包或修改其他 Agent 配置。把实测结果与冲突证据回传协作区；有差异先纠错，再升级共享知识。
