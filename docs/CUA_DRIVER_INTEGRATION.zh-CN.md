# Cua Driver 接入 AEP

更新：2026-10-31

## 结论

`trycua/cua` 已作为隔离的桌面执行 Provider 接入本机 AEP：

- 本地运行时：`.tools/cua-runtime`
- 已安装版本：`cua==0.9.0`、`cua-driver==0.38.0`
- MCP 探测：已通过 `initialize` + `tools/list`
- 当前目录状态：`TOOLS_DISCOVERED`，仍是 `candidate`，不是“已完成任意 Agent 派工”
- 本次探测未执行桌面输入，未触碰现有 Agent 会话

探测到的关键工具包括：`list_windows`、`get_accessibility_tree`、`get_window_state`、`launch_app`、`bring_to_front`、`click`、`type_text`、`press_key`、`hotkey`、`clipboard_*`、`get_desktop_state`、`browser_*`、`verify_state`、`run_steps` 等。

## 可复现安装与探测

在 AEP 根目录用 PowerShell 7：

```powershell
pwsh.exe -File .\scripts\install-capability-runtime.ps1
pwsh.exe -File .\scripts\install-cua-runtime.ps1 -Install -Register -Probe
```

脚本使用固定版本并创建独立运行时，不执行上游远程 `irm | iex` 安装器，也不覆盖已有 Python 环境。已有运行时再次执行 `-Install` 只会复用它；安装目标不完整时会停止并保留现场。

如只想重新注册/探测：

```powershell
pwsh.exe -File .\scripts\install-cua-runtime.ps1 -Register -Probe
```

## 与 AEP 的边界

1. AEP 仍是 Workspace、DAG、claim、快照、接力和事件证据的权威；Cua Driver 只是 MCP 桌面“手”。
2. `tools/list` 只证明工具可发现，不证明当前 Agent 会话已绑定。
3. 生产派工必须按统一证据链逐级完成：

   `CLIENT_BOUND → WINDOW_OR_TAB_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK`

4. Cua 的动作支持和后台可用性按平台/应用变化。上游的 exact refusal / background unavailable 必须进入 AEP 的失败分类，不得对同一动作盲重试。
5. 对视觉动作要绑定上游要求的单次 `capture_id`；截图可观察不等于会话身份正确。
6. Windows 桌面 E2E 需要交互式会话或 RDP。无人值守场景应优先使用原生 API/CLI、CDP、UIA 或已绑定的 Agent 后台通道；Cua 只能对其声明支持的动作接力。

## 权限与部署策略

Cua Driver 的 MCP 服务支持 `standard`、`bounded`、`unrestricted` 权限模式，以及显式的 existing-profile 授权。AEP 不在底座里替用户做业务审查，但会保留 WorkspaceID、事件 ID、窗口/标签身份和回读证据。要启动 unrestricted，必须由部署者显式传入上游参数，并在工作区审计中记录该选择。

默认不安装 `cua-perception`：其可选视觉扩展包含 AGPL-3.0-only 的 OmniParser 及其他第三方组件，不能在没有核对许可证和再分发义务时打包进 AEP。主仓库的不同子项目许可也并不完全相同，发布前应按实际组件记录来源、版本、许可证和 NOTICE。

## 生产晋级门槛

将 `cua_driver` 从 `candidate` 晋级为 `session-accepted` 前，必须留存一份同机证据：

- 原窗口唯一标识（进程、标题/自动化树或浏览器 profile）；
- 同一 `event_id` 的输入提交记录；
- 输入在原会话可见的回读；
- 目标 Agent 的明确 `AEP-ACK`；
- 结果回读或可解释失败（包括 exact refusal code）；
- 前后截图哈希、工具名和参数摘要。

没有这六项，只能报告 `TOOLS_DISCOVERED`，不能报告“已唤醒/已派工成功”。

## 上游致谢与引用

感谢 [trycua/cua](https://github.com/trycua/cua) 提供跨平台桌面执行、MCP 边界和动作支持账本。AEP 只通过独立运行时和 Provider Profile 接入，不复制上游源码；发布产物时保留上游许可证和第三方 NOTICE。

实施时参考了上游的 [Cua Driver README](https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/README.md)、[动作支持账本](https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/docs/action-support.md)、[MCP/Skills 协议说明](https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/docs/mcp-protocol-and-skills.md) 和 [Python 版本约束](https://raw.githubusercontent.com/trycua/cua/main/pyproject.toml)。
