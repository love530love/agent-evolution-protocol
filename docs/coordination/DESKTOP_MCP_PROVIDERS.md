# Windows 桌面 MCP 能力接入（2026-10-16）

这些本机能力值得接入 AEP，复用现有原会话、输入租约、事件 ID 和 ACK 约束。每个第三方服务独立启动和探测，构建成功、工具发现、控件可用、原会话投递和 Agent ACK 分别验收。

## 本次核验

本机 `.tools/mcp-windows-v1.3.28/bin/Sbroenne.WindowsMcp.exe` 已通过 MCP 初始化和工具发现，共 18 个工具，包含 `window_management`、`ui_snapshot`、`ui_find`、`ui_read`、`ui_type`、`ui_select`、`ui_click`、`keyboard_control` 等。SDK 使用隔离解释器中的 MCP 1.30.0。本轮只初始化并读取工具定义，没有调用桌面输入工具，没有向任何 Agent 发消息。

用户报告的另外四个构建项目尚未定位其源码或产物目录，因此保留待注册状态，不把用户报告的构建成功冒充本轮实机验证。与 2026-10-12 记录一致，工具存在也不证明 WorkBuddy/Qoder 的输入框能被 UIA 读取。旧审计和旧通道决策保留为历史，当前引入可探测的备用能力层。

| Provider ID | 项目 | 接入方式 | 目前状态 |
| --- | --- | --- | --- |
| sbroenne | sbroenne/mcp-windows | 直接启动本机 exe，stdio | 本机 18 工具发现通过 |
| shuyu | shuyu-labs/Windows-MCP.Net | 本机 exe 或 dotnet.exe + DLL | 等待真实目录和工具列表 |
| mcpcontrol | claude-did-this/MCPControl | node.exe + build/index.js，stdio | 等待真实目录和工具列表 |
| mario | mcp-windows-desktop-automation | node.exe + dist/index.js；required_paths 登记 AutoIt3 | 用户报告缺 AutoIt3；暂不作为可用通道 |
| cursortouch | CursorTouch/Windows-MCP | 本机已安装解释器入口或 uv.exe 启动命令 | 依赖同步、入口和运行状态待核验 |

## 统一命令

所有命令为 `python -m agent_evolution_protocol --coord-root <协作目录> coord-desktop ...`：

```powershell
python -m agent_evolution_protocol --coord-root .\coordination coord-desktop register --profile-file .\examples\desktop-provider.json
python -m agent_evolution_protocol --coord-root .\coordination coord-desktop probe --provider sbroenne --timeout 30
python -m agent_evolution_protocol --coord-root .\coordination coord-desktop inventory
python -m agent_evolution_protocol --coord-root .\coordination coord-desktop plan --operation locate --previous-state PROVEN_NOT_SENT
```

先修改示例中的本机路径。`command` 与 `sdk_python` 必须为存在的绝对可执行文件路径，`args` 为参数数组，`cwd` 可选。`sdk_python` 需要安装 MCP Python SDK；核心 AEP 仍不强制依赖 SDK。使用已有隔离环境即可；无需模型 API Key。服务端用 Node 或 .NET 均可，客户端协议统一为 MCP stdio。

`required_paths` 声明 AutoIt3、DLL 或入口 JS 等外部依赖，缺失时阻止启动。依赖齐全仍不等于运行健康。`bindings` 使用实际发现的精确工具名映射操作；可先留空，探测后再绑定并重新探测。示例工具名仅对应本次发现的 sbroenne 版本，不应照搬给其他项目。配置变化和超过 30 分钟的旧探测结果不会被调度计划采纳。

探测只做 initialize 和 tools/list，支持分页、有限超时、关闭进程和错误回执。注册、工具定义及回执保存在协作根的 `desktop_providers/` 下，本机路径不写入公共仓库。探测会启动第三方服务；只注册用户已选定的本机程序，不从网页或聊天内容自动安装或执行新程序。

## 调度与恢复

优先级是可配置的候选排序，默认优先控件级通道，再考虑其他实现；当前版本仅输出操作计划，不执行工具或隐式尝试备用通道。计划包含精确工具定义，后续适配器仍需结合 PID/窗口/原会话/模型标识、唯一控件和共享桌面输入锁。

原生客户端会话 API 或已验收的内置能力继续优先。浏览器任务遵循现有内置浏览器优先、AI Browser Bridge 补位的策略；桌面 MCP 用于桌面能力缺口。不同 Agent 可并行推理、读文件；影响前台、剪贴板、键鼠的操作必须串行持有全局输入租约，不能让五个服务同时抢输入焦点。

`NOT_STARTED`、`PROVEN_NOT_SENT` 才能生成备用计划。发送结果不明时 `plan` 返回 `VERIFY_PREVIOUS_ATTEMPT`，不提供再次发送候选。Commander 的 `DEFERRED` 回执会附备用定位计划，`CEASED` 回执保留先核验的阻断说明。计划不会把订单改成已发送或 ACK，也不会新开会话。

下一道实机验收：在唯一原会话确认当前模型与输入框，持有全局输入租约，发送一个带事件 ID 的最小测试指令，读回消息列表，再取得可归属为目标回复的 ACK。首次写入结果不明确就暂停恢复，不能靠切换服务重复按回车。
