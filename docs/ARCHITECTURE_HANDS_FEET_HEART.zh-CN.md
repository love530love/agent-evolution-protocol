# AEP「手—脚—心脏」能力架构与引入决策

更新：2026-10-31

这份文档把第三方执行生态映射到 AEP 的持久联邦工作区，不把“发现了工具”误报成“已经能够可靠派工”。

## 结论先行

AEP 的“心脏”已经存在：`federation/engine.py` 负责 Workspace、DAG、心跳、快照、接管和事件账本；
`coordination/claim_conflicts.py` 与 `coordination/execution_plane.py` 负责写入租约和派工证据闸门。

“手”也已有可用底座：`coord-desktop` 注册/探测本机 Windows-MCP、UIA、MCPControl、CursorTouch 等 provider，
`agent_mcp_registry.py` 为每个 Agent 管理独立的 MCP Bundle、连接器和会话定位线索。

当前真正未完成的是“脚”的生产级远程协议实现：

- AEP 的 `federation.adapter.Adapter` 是稳定边界，但真实 A2A HTTP/SSE/Push 适配器尚未验收；
- MCP 工具发现已可用，但工具发现、窗口定位、输入提交、目标 Agent ACK 仍必须分别取证；
- 多机事件总线和共享锁尚未落地，当前持久化账本以单机文件系统为事实源。

## 三层映射

| 层 | AEP 当前实现 | 第三方能力如何接入 | 生产判定 |
|---|---|---|---|
| 心脏：调度与恢复 | `federation/engine.py`、`snapshot.py`、`handoff.py`、`diagnosis.py`、`evolution.py` | Temporal 只作为可选 durable backend，不替换本地事件账本 | Workspace 恢复、DAG 接力和证据链已有测试；多机未验收 |
| 脚：协议与工具 | `federation/adapter.py`、`mcp_server.py`、`agent_mcp_registry.py`、`dispatch_contract.py` | A2A SDK 作为远程 adapter；MCP SDK 作为工具层；LiteLLM 作为可选模型路由 | AEP 事件、claim、snapshot 仍是权威；外部协议只做传输 |
| 手：桌面/软件执行 | `desktop_providers.py`、`desktop_runner.py`、`uia_transport.py`、`transport_cdp.py`、`execution_plane.py` | UIA/句柄优先，视觉/坐标兜底；Cua Driver 作为已完成本机工具发现的 MCP provider，Browser Use/Skyvern/UI-TARS/UFO2 等继续作为候选 provider | 必须达到同会话绑定→消息可见→同事件 ACK 的闭环 |

## 推荐的执行器优先级

1. 原生 API/CLI 或 Agent 自带浏览器能力；
2. 已绑定会话的 CDP/UIA/MCP 控件级操作；
3. `pywinauto`/`uiautomation` 句柄级定位；
4. UI-TARS/OmniParser 等视觉定位；
5. `pyautogui`/坐标动作仅作最后兜底，并且必须有前后截图和事件回读。

视觉模型不能替代会话身份。截图中“看到了输入框”只证明可观察，不证明消息发给了正确的 Agent 原会话。

## 第三方项目的接入策略

### 直接可复用，但保持可选

- `a2aproject/a2a-python`：实现 AEP→A2A 的 AgentCard、Task、状态和 artifact 映射；只放在可选 adapter 中，核心包不强制依赖。
- `modelcontextprotocol/python-sdk`：AEP MCP server 已有封装；继续保持 `mcp<2` 的兼容上界，等迁移验证完成再升级。
- `temporalio/sdk-python`：仅作为跨机器/跨进程部署的 durable backend 试验，不在单机默认路径启用。
- LiteLLM：只负责模型端点统一，不拥有 Workspace、claim 或桌面执行权限。

### 作为 Provider Catalog 候选，不复制源码

UI-TARS/Agent TARS、Cua Driver、OmniParser、UFO2、Agent-S、Browser Use、Skyvern、OpenAdapt、Open Interpreter、OpenHands、Appium、REA
已经登记在 `examples/execution-provider-catalog.json` 或 MCP Bundle 文档中。Cua Driver 已在本机隔离运行时完成 `initialize`/`tools/list`，但登记状态仍是 candidate；其余项目登记状态也是 candidate；
只有通过本机探测、会话绑定和真实任务回执，才能晋级 `task-tested`。

每个 provider 必须保留：上游仓库、版本/提交、许可证、启动命令、依赖清单、工具 schema、探测回执和失败日志。

## 统一 Computer-Use 回执

所有手部通道都必须映射到同一条证据链：

```text
CLIENT_BOUND
  → WINDOW_OR_TAB_LOCATED
  → INPUT_SUBMITTED
  → MESSAGE_VISIBLE
  → AGENT_ACK
  → TASK_RESULT_READBACK
```

`DeliveryEvidence` 位于 `coordination/execution_plane.py`；任何适配器不能跳级，不能把“进程启动”“工具列表成功”或“输入框出现文字”升级成 ACK。

## 分阶段落地路线

### L0：已完成

- Provider Catalog、Agent MCP Bundle、桌面 provider 探测；
- Workspace/DAG/快照/接管；
- 事件 ID、消息信箱、ACK/回读分层；
- claim gate、TTL、owner-only renew；
- UIA/CDP/MCP 失败后的证据保留和禁止盲重试。

### L1：下一步必须完成

1. 写一个本地回环 A2A adapter：把 AEP task-envelope 映射为 A2A Task，再把状态/artifact 映射回 AEP；
2. 用同一 event ID 跑 `submitted → working → completed/failed`，验证跨进程回读；
3. 将 A2A adapter 注册为普通 `federation.Adapter`，不让内核导入厂商 SDK；
4. 为 Computer-Use provider 增加 action receipt，记录窗口/标签身份、工具名、参数摘要、前后截图哈希。

### L2：可选增强

- OmniParser 或 UI-TARS 作为视觉定位 provider；
- OpenAdapt 作为录制/回放 provider；
- LiteLLM 作为模型路由；
- SQLite/DuckDB/向量索引只用于检索和复盘，不能替代事件账本。

### L3：多机部署才引入

- Temporal workflow backend；
- NATS/Redis Streams 事件总线；
- Postgres/对象存储；
- 跨主机身份、证书和 A2A endpoint discovery。

这些组件没有通过隔离试验前，不进入核心依赖，也不写成“已支持”。

## 许可证与致谢

AEP 通过 Provider Catalog、配置和 adapter 接入第三方项目，不复制其源码。发布或安装 provider 前，必须记录上游许可证、版本和来源；
运行时资产不应把第三方密钥、私有会话或本机路径提交到仓库。感谢 A2A、MCP、Temporal、UI-TARS、Microsoft UFO、Browser Use、OpenHands、OpenAdapt、REA 等上游项目提供可复用的协议和执行思路。

## 验收命令

```powershell
pwsh.exe -File .\scripts\bootstrap.ps1 -CoordRoot coordination
aep --coord-root coordination coord-provider-catalog --status candidate
aep --coord-root coordination coord-desktop inventory
```

上述命令只证明配置/探测状态。生产晋级还必须附带同会话消息可见、Agent ACK 和结果回读证据。
