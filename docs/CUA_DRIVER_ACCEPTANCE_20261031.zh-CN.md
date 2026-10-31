# Cua Driver 本机接入验收记录（2026-10-31）

## 验收范围

本次验收只覆盖“可复现安装、二进制自检、MCP 工具发现、AEP Provider 计划生成”。没有向任何真实 Agent 会话发送消息，也没有执行桌面输入。

## 结果

| 项目 | 证据 | 结果 |
|---|---|---|
| 隔离运行时 | `.tools/cua-runtime` | 通过 |
| Python 兼容性 | Cua runtime 使用 CPython 3.13 | 通过 |
| 包版本 | `cua==0.9.0`、`cua-driver==0.38.0` | 通过 |
| Driver 自检 | `cua-driver doctor --json`：interactive session、UI Automation、EnumWindows 均 `ok` | 通过 |
| MCP 初始化与发现 | AEP `coord-desktop probe --provider cua_driver` | `TOOLS_DISCOVERED` |
| 工具数量 | `tools/list` | 63 |
| AEP 操作计划 | `coord-desktop plan --operation click/type` | `PLAN_ONLY`，已匹配 `click` / `type_text` schema |
| 真实派工 | 原 Agent 会话输入、可见、ACK、结果回读 | 未执行 |

## 已发现的关键工具

`list_windows`、`get_window_state`、`verify_state`、`launch_app`、`bring_to_front`、`click`、`type_text`、`press_key`、`hotkey`、`clipboard_*`、`get_accessibility_tree`、`get_desktop_state`、`browser_*`、`run_steps`、`run_script`。

## 结论等级

当前状态：`candidate / TOOLS_DISCOVERED`。

这证明了 Cua Driver 已成为 AEP 可调度的桌面 MCP 候选，不能推导出“任意 Agent 已被唤醒”或“消息已经发送”。晋级到 `session-accepted` 仍需在同一个真实原会话里取得：

1. `CLIENT_BOUND` 与唯一窗口/标签身份；
2. `INPUT_SUBMITTED`；
3. `MESSAGE_VISIBLE`；
4. 目标 Agent 的同事件 `AEP-ACK`；
5. `TASK_RESULT_READBACK` 或明确的 exact refusal；
6. 前后截图哈希、工具名和参数摘要。

## 重现命令

```powershell
pwsh.exe -File .\scripts\install-cua-runtime.ps1 -Install -Register -Probe
& .\.tools\cua-runtime\Scripts\cua-driver.exe doctor --json
```

## 许可与来源

接入使用上游二进制/包，不复制源码。发布前应按实际组件保留上游许可证和 NOTICE；尤其不要默认安装可选感知扩展，先核对其中 AGPL-3.0-only 与其他第三方组件义务。

来源核对：[上游仓库](https://github.com/trycua/cua)、[上游许可证说明](https://github.com/trycua/cua#license)。
