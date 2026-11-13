# agent-desktop 与 Super-agent 实机能力审计（2026-11-12）

## 结论先行

**AEP 已有相当完整的“控制面”和多条桌面执行通道，但尚未拥有一条对 Qoder / Qoder CN / WorkBuddy / WorkBuddy AI / CodeBuddy CN 全部实机验收过的生产级“手脚”闭环。**

本次只读实测证明：

- [Vercel `agent-desktop`](https://github.com/vercel-labs/agent-desktop) 已在本机隔离安装、可发现窗口、可操作标准 Windows UI；它不是空壳。
- 它目前**不能直接解决**本机主要 Electron Agent 的会话投递：目标窗口的 UIA 树要么超时，要么只给出无输入框/发送按钮的空壳节点。
- AEP 不能把“工具可发现”“窗口可枚举”“CLI 返回 `ok`”升级为派工成功；真正的晋级仍是：

```text
SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

这里的 “Super-agent” 按当前 GitHub 可核验项目解释为
[`deepseek-ai/dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype)。
若意指另一仓库，应以精确 URL 重做比对。

---

## 一、AEP 当前已经具备什么

|层|已实现的能力|当前证据状态|
|---|---|---|
|控制面|Workspace/事件、DAG、claim/lease、原会话锚定、ACK 与结果状态机、可追溯回执|源码与回归已覆盖；不等于第三方 Agent 已接单|
|Windows 发现|进程/窗口枚举、UIA、OCR、CDP、共享 session lease、Desktop Runner 与 Supervisor|本机可运行；不同客户端的会话定位能力不同|
|桌面 Provider|`cua_driver`、`sbroenne`、`shuyu`、`mcpcontrol`、`mario`、`cursortouch`、`agent_desktop` 已注册|均为 `TOOLS_DISCOVERED`，不是 `session-accepted`|
|严格原会话门禁|受限 session/model/selector/composer 区、fingerprint、HWND、UIA runtime 绑定；写入前二次验证|v0.6.33 源码/CI 已通过；每个目标客户端仍须实机标定|
|浏览器/无头增强|CDP、Browser Bridge 以及原生 CLI/MCP 作为速度路径|与桌面原会话的统一绑定仍未完成|

相关代码入口：

- `src/agent_evolution_protocol/coordination/uia3.py`、`ui_fallback.py`、`commander.py`：原会话 UIA/OCR/CDP 路由和严格门禁；
- `src/agent_evolution_protocol/coordination/desktop_providers.py`：MCP provider 注册/探测/计划；
- `src/agent_evolution_protocol/coordination/external_tools.py`：第三方 CLI 的隔离发现和探测；
- `src/agent_evolution_protocol/coordination/external_runner.py`：受 lease 约束的外部进程执行；
- [`HANDS_AUDIT_20261107.md`](../HANDS_AUDIT_20261107.md)：跨路由 lease、CDP、监督器与严格会话门禁的已修复问题及未过门槛。

---

## 二、agent-desktop：本机实测，而非 README 推断

### 2.1 上游能力与适配价值

上游是 Apache-2.0 的跨平台 CLI/C 库，调用 Windows UI Automation、macOS AX 或 Linux AT-SPI；它把 snapshot 中的可操作元素绑定为短寿命 ref，要求调用方执行 observe → act → verify 循环。

它适合 AEP 的原因：

1. 支持 `snapshot`、`find`、`click`、`set-value`、`clear`、`select`、窗口控制、等待、截图、剪贴板等，并在 1.2.0 提供 stdio MCP server；
2. 原生 JSON envelope 包含错误、是否可安全重试、动作交付语义，能映射到 AEP 的 `PROVEN_NOT_SENT` / `UNCERTAIN` / `MESSAGE_VISIBLE` 证据等级；
3. Windows 中可在不抢前台的条件下对 UIA ref 做语义动作；需要物理键入、焦点或鼠标的动作会明确要求 headed 模式。

上游资料：

- [README：refs、MCP、命令与跨平台边界](https://github.com/vercel-labs/agent-desktop)
- [Windows 平台行为、UIPI、headless 限制与 Electron 说明](https://agent-browser.dev/desktop/platforms/windows)
- [JSON envelope 与 `ok` 不等于业务效果的说明](https://agent-browser.dev/desktop/json-output)

### 2.2 2026-11-12 只读本机验证

执行环境：`K:\PythonProjects5\agent-evolution-protocol` 的隔离依赖目录。

|检查|结果|说明|
|---|---|---|
|二进制|`agent-desktop 1.2.0`，Windows/x64|已固定在 `.tools/external-agent-tools`，不是 PATH 偶然命中|
|`status`|成功；UIA 可用，interactive session 可用|能力存在，不代表目标客户端控件树存在|
|AEP Provider|已登记，历史 MCP tools/list 发现 67 工具|状态仍是 `TOOLS_DISCOVERED`|
|`list-windows`|成功发现 Qoder、Qoder CN、WorkBuddy、WorkBuddy AI、CodeBuddy CN 等窗口|窗口发现不是会话证明|
|文件资源管理器快照|成功，完整 UIA 树含 **380** refs|证明工具及本机 Windows UIA 链路本身可用|
|五个目标 Agent 的普通 interactive snapshot（4 秒）|全为 `TIMEOUT`|无一能直接当作可写 UIA 控件树|
|Qoder 普通 snapshot（25 秒）|仍为 `TIMEOUT`|已按上游 Electron 冷启动建议扩大观察时限后复测|
|五个目标 Agent 的 `--force-electron-a11y` 快照|Qoder/Qoder CN/CodeBuddy CN 为 0 refs；WorkBuddy/WorkBuddy AI 只有 1 个无名 group ref|没有发现 composer、发送按钮或可唯一定位的会话行；不能输入、更不能发送|

本次没有聚焦窗口、键入、点击、启动应用、发送消息或改变目标会话。所有验证均为只读观察。

### 2.3 新增 AEP 可操作性探针的实机回表

审计后已实现并执行：

```powershell
aep --coord-root coordination coord-desktop capability-probe --agent <agent> --provider agent_desktop --timeout 4
```

该命令会把 Windows roster 的唯一 `PID + title` 与 `agent-desktop`
的 `window_id + process_instance` 对齐，再分别采集普通与 Electron
强制辅助树快照。它**只读**；不会抢焦点、输入、点击或发送。2026-11-12
本机五个实际目标的回表如下：

|Agent|会话锚定状态|普通树|强制 Electron 树|编辑器候选|结论|
|---|---|---|---|---:|---|
|Qoder|`CURRENT`|`TREE_TIMEOUT`|`TREE_OPAQUE`（0 refs）|0|不可用，不能键入|
|Qoder CN|`STALE`|`TREE_TIMEOUT`|`TREE_OPAQUE`（0 refs）|0|不可用，且需重做原会话锚定|
|WorkBuddy|`STALE`|`TREE_TIMEOUT`|`TREE_PARTIAL`（仅标题栏 1 ref）|0|不是 composer，不能键入|
|WorkBuddy AI|`UNCONFIGURED`|`TREE_TIMEOUT`|`TREE_PARTIAL`（仅标题栏 1 ref）|0|不是 composer，不能键入|
|CodeBuddy CN|`UNCONFIGURED`|`TREE_TIMEOUT`|`TREE_OPAQUE`（0 refs）|0|不可用，不能键入|

特别说明：旧版把“`ref_count > 0`”称为 `TREE_USABLE` 会把 WorkBuddy 的
标题栏误报成可派工通道。新探针要求出现编辑器角色（如 `textbox` / `edit` /
`document`）才会标为 `TREE_USABLE`；只有 group/title-bar 等残片一律是
`TREE_PARTIAL`。即使未来出现 `TREE_USABLE`，顶层也只会返回
`SESSION_UNATTESTED`，绝不直接称为“已派工”。

### 2.4 直接结论

`agent-desktop` **应该保留并深化接入**，但其角色应为：

```text
标准 UIA 应用：首选结构化桌面执行器
目标 Electron Agent：只读探针 / 可能的 future route
UIA 为空、超时或不唯一：明确转 CDP、原生 API、已验收 Browser Bridge、或 AEP 已标定的 UIA/OCR fallback
```

它不能把没有暴露辅助功能控件的应用“变成可控”；AEP 需要记录 `TREE_USABLE`、`TREE_OPAQUE`、`TREE_TIMEOUT`、`ELEVATION_BLOCKED` 等实际能力状态，而不是仅记录 provider 已安装。

---

## 三、Super-agent：可以借鉴什么，不能把它当什么

[`dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype) 是面向**单一 GitHub 仓库**的 DeepSeek Harness 研究原型：GitHub/可选飞书事件形成 Trigger，经过 Eval/Policy 产生 Ticket，再把 Ticket Assignment 交给独立 worker session 和工作目录；结果回到原始讨论或 PR。

它的 README 明确声明“研究原型、非官方产品、非生产就绪”，且公开版本不等同内部实现；它采用 MIT 许可证。故它是**设计/试验参考**，不是应直接替换 AEP 内核的生产依赖。

### 值得吸收的四项设计

1. **Durable effect queue**：副作用在事务提交后入队；key 去重、失败指数退避、按 effect type 管理并发。这正好补 AEP 的“计划任务正在运行”与“真实订单是否前进”之间的空档。
2. **Trigger / Ticket / Assignment 分离**：事件、逻辑工作项、具体执行者不复用同一个状态对象，有利于跨重启换人接力。
3. **每 Ticket 独立会话与目录**：长任务不会被无关对话污染，恢复时能带确定的工作目录和结构化交接。
4. **delivery 的显式重试/退避与队列可见性**：适合 AEP 的消息投递，不把短暂服务失败误判为目标已收到。

### 不能直接照搬的部分

- 它的 Executor 是 DeepSeek Harness session，不是 Qoder/WorkBuddy/CodeBuddy 的原桌面会话；不能替代会话定位或 GUI 输入；
- 公开仓库目前只面向单仓库，部署依赖 DSH、Node/pnpm、tmux、GitHub CLI、模型提供商和专用 GitHub Agent 账号；
- 其作者自己要求先审阅、再适配，不能把 prototype 直接作为 AEP 的无人值守生产基座；
- AEP 的 Workspace 多项目、跨厂商 Agent、桌面原会话锚定、共享 lease 和五段验收链仍须由 AEP 保持权威。

上游一手资料：

- [README 与研究原型声明](https://github.com/deepseek-ai/dsh-superagent-prototype)
- [Domain model：Trigger / Ticket / Assignment](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/001-domain.md)
- [Execution：worker slots、恢复与任务阶段](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/003-execution.md)
- [Architecture：durable effects 与 delivery backoff](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/007-architecture.md)

已有的深入设计比对见 [`DSH_SUPERAGENT_20261102.zh-CN.md`](DSH_SUPERAGENT_20261102.zh-CN.md)。

---

## 四、下一轮真正应做的事（按收益排序）

### P0：把 provider 发现升级为每客户端“可操作性探针”（已完成第一条 route）

现已新增只读 `coord-desktop capability-probe --agent <id>`，首版接入
`agent_desktop`，并将最小证据写入
`coordination/desktop_providers/capabilities/agent_desktop--<agent>.json`：

1. 读取固定 profile 与当前 window/process anchor；
2. 用 `PID + title` 精确对齐上游 `window_id + process_instance`；
3. 分别尝试普通 UIA 与 Electron 强制辅助树，输出
   `TREE_USABLE` / `TREE_PARTIAL` / `TREE_OPAQUE` / `TREE_TIMEOUT` /
   `WINDOW_NOT_BOUND` 等状态；
4. 只写 ref 数量、编辑器/发送候选数量、错误短码，不落完整 UI 树或聊天文本；
5. **不输入、不聚焦、不发送**，因而可定时运行而不干扰用户；
6. 当前还没有 CDP、Browser Bridge、Cua 的同类 profile probe；这是 P0
   的下一小步，而不是已经完成的能力。

### P1：实现 `agent-desktop` 的 AEP 受控 Driver

不是把 67 个 MCP 工具裸露给调度器，而是实现一层只接受 AEP order 的 driver：

```text
session lease
  → snapshot/ref + process_instance/window_id
  → 会话/模型/composer 证明
  → input action
  → fresh snapshot 证明消息可见
  → transcript/ACK 读回
  → result parser
```

规则：ref 过期、UIA 空树、任何不唯一或动作结果 `delivered_unverified` 均进入 `UNCERTAIN`，不自动换通道重发；只有明确 `PROVEN_NOT_SENT` 才允许重试。

### P2：Electron 特化路径，而不是反复盲等 UIA

针对每个客户端逐项完成：

1. 是否能通过启动参数或应用设置开启 Chromium accessibility tree；
2. 是否能以已验证 `CDP target_id + URL + profile + PID` 调用；
3. 是否存在官方本地 API/CLI/MCP；
4. 无上述通道时，才进入 v0.6.33 的严格 UIA/OCR profile 标定；
5. 每个步骤必须落一份**原会话** `MESSAGE_VISIBLE → AEP-ACK → RESULT_READBACK` 证据。

### P3：从 Super-agent 吸收 durable effect queue，而非引入 DSH 运行时

为 AEP 新建 `effect_outbox`：`effect_id + workspace_id + order_id + kind + attempts + next_due + receipt`。这能把“心跳/定时器启动了”变成“某一精确订单是否被可靠地推进”，同时保留 AEP 的事件链、lease 与多工作区隔离。

---

## 五、生产门槛

任一 Agent 只有同时满足以下全部条件，才可在 roster 中标为 `SESSION_ACCEPTED`：

1. 当前窗口/标签与 Workspace、Agent、原会话 anchor 唯一绑定；
2. 选择的执行 route 已完成同机只读 capability probe；
3. 输入前检查同一 session lease；
4. 同一 event ID 真实达到 `INPUT_SUBMITTED` 和 `MESSAGE_VISIBLE`；
5. 目标在原会话产生同 event ID 的 `AEP-ACK`；
6. 有任务产物或明确失败的 `TASK_RESULT_READBACK`；
7. 所有证据可在进程重启后从 coordination root 读回。

未满足时，AEP 必须报告具体 route/证据状态，而不是写“已唤醒”“已派工”或以桌面窗口存在代替验收。
