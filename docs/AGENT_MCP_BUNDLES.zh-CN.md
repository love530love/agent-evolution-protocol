# Agent MCP Bundle：按 Agent 配置能力与会话适配器

## 结论

可以为 Qoder、Qoder CN、WorkBuddy、WorkBuddy AI、CodeBuddy CN、ChatGPT、ClawChat
以及后续加入的任意 Agent 配置独立 MCP Bundle。但需要区分三件事：

1. MCP Server 是工具提供者，不等于目标 Agent 的原生会话；
2. `coord-agent-mcp register` 只登记配置，不会自动打开程序或发送消息；
3. 只有唯一会话定位、输入/发送、消息可见、同事件 ACK 和回读证据全部成立，才算派工成功。

## 推荐拓扑

每个 Agent 由一个独立 profile 描述，但服务器可以复用：

```text
Agent profile
 ├─ AEP federation MCP     工作区、任务、快照、派工、ACK、回读
 ├─ desktop MCP            UIA/控件级窗口定位与输入
 ├─ browser MCP            CDP/浏览器标签与 DOM 操作
 ├─ REA                     逆向、运行行为和底层证据
 └─ native adapter          厂商 API、CLI、ClawChat 或本地文件信箱
```

从 v1 起，Bundle 不再只有 MCP server，还包含三类可执行资产：

- `servers`：MCP/stdio/http 等工具进程；
- `skills`：Agent 特化的工作流入口、参数和验收提示；
- `connectors`：应用级连接器，负责启动、会话定位、回读或 REA 取证。

统一 Schema：`schemas/agent-capability-bundle-v1.schema.json`。
本地示例：`examples/capability-bundle.qoder.json`。

Bundle 的职责边界是：

```text
Skill       = 怎么做
Connector   = 通过什么应用/通道做
MCP Server  = 能调用哪些底层工具
AEP Kernel  = 是否允许做、在哪个会话做、如何证明做成
```

因此，特化包可以大胆提供能力，但不能自行把“启动成功”“输入成功”升级成“已接单”；最终证据仍由 AEP Kernel 统一判定。

工作区通过 `AEP_WORKSPACE_ID` 和 `AEP_AGENT_ID` 注入每个 MCP Server。这样同一个
服务器进程不能把一个工作区的任务误写入另一个工作区。

## 当前接入层

- `sbroenne/mcp-windows`：控件名优先的 Windows UI Automation；适合窗口、输入框、按钮和回执定位。
- `Windows-MCP.Net`：.NET Windows 桌面 MCP，可作为第二 UIA/OCR 备选。
- `MCPControl`：Node/TypeScript 桌面控制备选。
- `mcp-windows-desktop-automation`：需要 AutoItX 的桌面备用通道。
- `CursorTouch/Windows-MCP`：Python/uv 通道，可用于另一套视觉/桌面探测。
- `morluto/rea`：只在需要逆向制品、运行行为或二进制证据时调用；它不是消息派发器。
- “anything CLI”类工具：不要硬编码项目名；将其作为 `command + args + cwd` 的 MCP/CLI server 注册，先探测工具，再进入派工计划。

Windows UIA 项目中的控件级操作通常比坐标点击更适合生产回归。`sbroenne/mcp-windows`
公开说明其按名称通过 Windows UI Automation 查找控件，减少 DPI、主题、分辨率和窗口位置变化的影响；
`Windows-MCP.Net` 和 `MCPControl` 可作为不同实现的备份通道。它们都仍需通过本项目的
session binding、共享输入租约和 ACK 验收。

## 命令

```powershell
# 登记一个 Agent 的 MCP Bundle（仅写配置）
python -m agent_evolution_protocol --coord-root .\coordination `
  coord-agent-mcp register --profile-file .\examples\agent-mcp-profile.qoder-cn.json

# 查看已登记 Bundle 和本机缺失依赖
python -m agent_evolution_protocol --coord-root .\coordination coord-agent-mcp inventory

# 为一个 Workspace 生成可导入 Agent 客户端的 mcpServers 配置
python -m agent_evolution_protocol --coord-root .\coordination coord-agent-mcp render-config `
  --agent qoder_cn --workspace-id W-0123456789abcdef01234567

# 生成派工计划；没有原会话 ID 时会明确返回 SESSION_BINDING_REQUIRED
python -m agent_evolution_protocol --coord-root .\coordination coord-agent-mcp plan `
  --agent qoder_cn --workspace-id W-0123456789abcdef01234567 `
  --operation dispatch --session-id qoder-session-001
```

### 特化 Capability Bundle

```powershell
python -m agent_evolution_protocol --coord-root .\coordination `
  coord-agent-mcp register --profile-file .\examples\capability-bundle.qoder.json
```

登记后可通过 `inventory` 查看每个 Agent 的 MCP server、skill、connector 数量；通过
`render-config` 生成带 `AEP_WORKSPACE_ID`、`AEP_AGENT_ID` 的配置，再单独执行
session bind 和 ACK 验收。

`render-config` 的输出需要由对应 Agent 客户端导入。AEP 不会猜测厂商配置文件路径，
也不会因配置生成而声称已唤醒 Agent；真实发送必须继续走 `coord-wake`、`coord-commander`
或已有 UIA/CDP/native adapter，并取得回执。

## 适配器优先级

```text
native_api → aep_mcp → desktop_mcp → uia → cdp → clawchat → mailbox
```

这不是盲目重试序列。一次通道产生 UNKNOWN/已输入未确认时，必须先读回当前会话并核对事件 ID，
不能直接换通道重复发送。所有前台键鼠动作仍持有 workspace/session lease。

## 真实验收门槛

每个 Agent 都需要单独生成证据：

1. 程序/窗口发现；
2. 唯一原会话绑定；
3. MCP 工具初始化与工具定义发现；
4. 输入框定位和消息可见；
5. 目标 Agent 对同一事件 ID ACK；
6. 回读任务状态、产物或下一步；
7. 进程重启后能用 Workspace 快照恢复。

缺少任意一项，状态保持 `REGISTERED`、`TOOLS_DISCOVERED`、`SESSION_BINDING_REQUIRED` 或
`WAKE_QUEUED_UNVERIFIED`，不能标记为已接入生产。

## 开源项目的引入原则

不把第三方项目源码复制进 AEP；通过 profile、锁定版本、许可证记录和探测回执引用它们。
后续若需要自包含发行，再增加可选 provider bundle 和离线安装器，保留上游版本、SHA256、
运行时和许可证清单。REA、Windows MCP、MCPControl 等负责能力，AEP 负责工作区、会话、派工、
接力、证据和进化状态。
