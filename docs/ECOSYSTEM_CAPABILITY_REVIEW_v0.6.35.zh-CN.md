# AEP 能力与外部生态复盘 v0.6.35

更新日期：2026-11-15。本文是 `agent-evolution-protocol` 的能力总索引：回答
“项目已经能做什么、从哪里借鉴、哪些工具实际接入、哪些仍只是候选”。它不把
源码下载、工具发现、窗口出现或测试通过写成真实 Agent 已接令。

## 1. 判定口径

| 状态 | 含义 | 不代表什么 |
| --- | --- | --- |
| **核心已实现** | AEP 源码、测试和版本中存在该机制 | 外部客户端已经配置或运行 |
| **本机已探测** | 已在隔离运行时完成版本/初始化/工具枚举等最小验证 | 能定位正确原会话或能安全写入 |
| **会话已验收** | 已绑定唯一会话并完成只读回表 | 已派工、已 ACK 或任务已完成 |
| **生产闭环** | 同一事件具备输入、消息可见、目标 ACK、结果回读 | 对其他客户端也天然有效 |
| **参考/候选** | 已研究用途或接口，尚未成为默认依赖 | 已安装、已集成或获信任 |

真实桌面派工的唯一完成链为：

```text
SESSION_LOCATED → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

## 2. AEP 已有能力

| 能力面 | 已实现机制 | 主要入口/资料 | 当前证据级别 |
| --- | --- | --- | --- |
| 工作区与协作协议 | Workspace、任务、DAG、快照、交接、工件、哈希链、事件账本 | `federation/`、`coord-*` CLI、[持久联邦宪章](PERSISTENT_FEDERATION_WORKSPACE_ENGINE_CHARTER.zh-CN.md) | 核心已实现；外部 Agent 接力需逐家验收 |
| 协作治理 | 红皇后/鲶鱼/创造性破坏、挑战、反证、评审独立性、积分与作战角色 | [协同推理框架](COLLABORATIVE_REASONING_FRAMEWORK.zh-CN.md)、[红皇后复盘](coordination/RED_QUEEN_CATFISH_CREATIVE_DESTRUCTION_RETROSPECTIVE_20260928.md) | 核心已实现 |
| 派工与防错 | `dispatch_gate`、event ID、输入前改派、租约、冲突/TTL、停火/回退、订单与回执 | [派工标准](DISPATCH_STANDARD.zh-CN.md)、`coord-commander` | 核心已实现；不等于真实发送 |
| 原会话证明 | 会话指纹、原会话 attestation、路由校准、窗口/PID/模型/标记约束 | [路线校准](coordination/CLIENT_ROUTE_CALIBRATION.md)、[严格证明](STRICT_ORIGINAL_SESSION_ATTESTATION.zh-CN.md) | 机制/测试已实现；生产绑定仍待逐客户端登记 |
| 桌面执行 | UIA、OCR、几何兜底、输入/发送/回读分层、全局输入租约 | [人类 UI 兜底](coordination/HUMAN_UI_FALLBACK.md)、[生产桌面派送](PRODUCTION_DESKTOP_DELIVERY.md) | 机制已实现；真实闭环未普遍验收 |
| 浏览器执行 | CDP/浏览器适配器、标签/target 绑定、回读证据模型 | [浏览器适配器](BROWSER_ADAPTERS.zh-CN.md) | 核心/适配器框架已实现 |
| MCP 能力包 | `servers + skills + connectors + observability`、每 Agent/Workspace 注入、计划而非隐式执行 | [Agent MCP Bundle](AGENT_MCP_BUNDLES.zh-CN.md) | 核心已实现；登记不等于客户端已导入 |
| 进程与常驻治理 | Windows Job 收束 probe、MCP run-ID 账本、心跳、Python supervisor、watcher READY/单例 | [MCP 生命周期](coordination/MCP_PROCESS_LIFECYCLE.md) | 本机机制与回归已验收 |
| 记忆与数据 | JSON/Markdown 事件、SQLite/FTS5、DuckDB analytics、会话/命令/证据索引 | [内存存储](MEMORY_STORAGE.md)、`analytics` optional extra | 核心与可选能力已实现；不声明云端知识库 |
| 逆向与诊断 | REA capability、命令经验库、失败分类、桌面/MCP/运行时证据 | [REA 能力](coordination/REA_CAPABILITY.md)、[命令记忆](coordination/COMMAND_MEMORY.md) | 接口/资料已实现；按任务调用 |

v0.6.35 的本地回归记录为 `1744 passed, 3 skipped, 1018 subtests passed`。它验证
协议和执行框架，不取代外部 Agent 的生产 ACK。

## 3. 已接入或已在本机探测的外部能力

这些项目通过隔离运行时、Provider Profile、MCP/CLI 适配器或可选依赖接入；AEP
不复制其源码，也不把它们升级为默认可信执行者。

| 项目/标准 | 地址 | AEP 用途 | 接入状态与边界 |
| --- | --- | --- | --- |
| Model Context Protocol Python SDK | <https://github.com/modelcontextprotocol/python-sdk> | AEP Federation MCP、桌面 Provider stdio client | `desktop` optional extra；MCP 调用/工具发现不等于消息送达 |
| A2A Python SDK | <https://github.com/a2aproject/a2a-python> | 异构 Agent Card、任务/状态/回调的标准适配方向 | `a2a` optional extra；当前跨进程适配器仍需真实服务端验收 |
| Temporal Python SDK | <https://github.com/temporalio/sdk-python> | 长任务耐久执行、重试、定时器、跨重启恢复的可选后端 | `durable` optional extra；不替换本地离线核心 |
| DuckDB | <https://github.com/duckdb/duckdb> | 结构化分析、指标、向量/分析扩展的可选数据平面 | `analytics` optional extra；不是唯一权威任务库 |
| SQLite FTS5 | <https://www.sqlite.org/fts5.html> | 本地全文检索、会话/文档索引的轻量基础 | 随 SQLite 能力使用；需由工作区边界隔离 |
| Cua / Cua Driver | <https://github.com/trycua/cua> | `list_windows`、可访问性树、启动、前台、键鼠、浏览器与 `run_steps` 等桌面手脚 | 隔离 `.tools/cua-runtime` 已探测为 `candidate`；详见 [Cua 接入](CUA_DRIVER_INTEGRATION.zh-CN.md) |
| sbroenne mcp-windows | <https://github.com/sbroenne/mcp-windows> | Windows UIA 控件查找、读取、输入、点击 | 本机工具发现通过；未因此宣称任何会话写入成功 |
| Windows-MCP.Net | <https://github.com/shuyu-labs/Windows-MCP.Net> | .NET Windows UIA/OCR 备用 Provider | 本机工具发现通过 |
| MCPControl | <https://github.com/claude-did-this/MCPControl> | Node/TypeScript 桌面控制备用 Provider | 本机工具发现通过 |
| mcp-windows-desktop-automation | <https://github.com/mario-andreschak/mcp-windows-desktop-automation> | AutoItX 兼容的桌面自动化备用通道 | 本机工具发现通过；依赖路径需逐机核验 |
| CursorTouch Windows-MCP | <https://github.com/CursorTouch/Windows-MCP> | Python/uv 桌面 Provider 与视觉/控件探测 | 本机工具发现通过；probe 已纳入 AEPProbe Job 收束 |
| REA | <https://github.com/morluto/rea> | 二进制/运行时/协议取证与排障 | 作为按需 Connector，不充当消息派发器 |
| PowerToys | <https://github.com/microsoft/PowerToys> | Windows 快捷工具、窗口/文本工作流辅助 | 仅作为可选系统能力，不作为 AEP 权威执行通道 |
| Qwen3 0.6B | <https://huggingface.co/Qwen/Qwen3-0.6B> | 本地轻量路由/结构化意图辅助 | 本机开发模型；不得把本地推理输出等同操作回执 |

本机桌面 Provider 的具体探测数量、启动方式和限制见
[桌面 MCP Provider 清单](coordination/DESKTOP_MCP_PROVIDERS.md)。第三方许可、版本和
NOTICE 应在任何再分发/安装包构建时按**锁定 revision**重新核对。

## 4. 已借鉴设计，但尚未作为默认依赖的项目

| 项目 | 地址 | 借鉴点 | 建议接入位置 | 当前状态 |
| --- | --- | --- | --- | --- |
| Browser Use | <https://github.com/browser-use/browser-use> | 浏览器 Agent、真实 Chrome/DOM/视觉组合 | Browser Connector | 候选；先完成 tab/target 与回读验收 |
| Skyvern | <https://github.com/Skyvern-AI/skyvern> | 长流程浏览器会话、表单与持久会话思路 | 企业浏览器 Provider | 候选；不直接接管 AEP 调度 |
| LaVague | <https://github.com/lavague-ai/LaVague> | World Model / Action Engine 分离、规格驱动测试 | 浏览器测试 Connector | 候选 |
| UI-TARS / UI-TARS Desktop | <https://github.com/bytedance/UI-TARS> / <https://github.com/bytedance/UI-TARS-desktop> | 视觉 GUI 定位、端侧 GUI Agent、连续动作 | UIA 不可读时的视觉 Provider | 候选；单显示器/模型/运行时限制需实测 |
| Microsoft UFO | <https://github.com/microsoft/UFO> | Windows UIA + 视觉混合、Host/App 分层、桌面 AgentOS | Windows 执行器架构参考 | 候选；优先吸收架构而非替换核心 |
| OmniParser | <https://github.com/microsoft/OmniParser> | 屏幕元素解析、图标/控件语义、SoM | 视觉解析共享服务 | 候选；许可证和模型部署单独评估 |
| Agent S | <https://github.com/simular-ai/Agent-S> | 分层 GUI 规划与 OSWorld 评测方向 | GUI 规划/评估参考 | 候选 |
| OpenAdapt | <https://github.com/OpenAdaptAI/OpenAdapt> | 人类演示到可回放程序 | 已验收重复流程的低 token 回放 | 候选；回放前仍要校验会话锚点 |
| Open Interpreter | <https://github.com/OpenInterpreter/open-interpreter> | Shell/文件/代码执行的本地 Agent | Terminal Connector | 候选；必须回传命令、退出码、diff、工件哈希 |
| OpenHands | <https://github.com/All-Hands-AI/OpenHands> | 长程工程 Agent、checkpoint、工程任务面 | Engineering Agent Connector | 候选；不替代 AEP Workspace/证据链 |
| Self-Operating Computer | <https://github.com/OthersideAI/self-operating-computer> | 截图-决策-动作闭环、视觉兜底实验 | 研究/对照基线 | 仅参考 |
| Appium | <https://github.com/appium/appium> | 移动端设备控制与测试工件 | Mobile MCP/Connector | 候选 |
| Agent Desktop | <https://github.com/vercel-labs/agent-desktop> | Agent 桌面工作区和人类可视回表设计 | GUI/可观测性参考 | 已调研，未纳入运行时 |
| t3code | <https://github.com/pingdotgg/t3code> | 代码 Agent 桌面交互与会话体验 | 产品交互参考 | 已调研，未纳入运行时 |
| LobeHub | <https://github.com/lobehub/lobehub> | Agent/插件/会话 UI 生态 | 仅 UI/生态参考 | 未接入；项目保持自主管理 |
| ClawChat / Clawling | <https://github.com/clawling> | 人类可视化 Agent roster/chat | 独立 GUI 通道探索 | 未作为 AEP 权威通道；此前认证路径不稳定 |

## 5. 采纳原则与技术边界

1. **AEP 始终是协调权威。** 外部项目提供手、脚、视觉、浏览器、终端或 UI；任务状态、
   WorkspaceID、会话锚点、claim、事件、证据和交接仍由 AEP 保存。
2. **连接器先于全家桶。** 优先写窄而可测的 `Connector/Provider Profile`，不把重型平台的
   调度内核嵌入进来，避免双重任务库与双重真相。
3. **UIA/DOM 优先，视觉补位。** 控件/DOM 能唯一定位时优先使用；视觉模型只在 UI 树不可读
   时参与，并必须带截图、动作和会话锚点证据。
4. **动作与事实分离。** `click/type/send` 返回成功只记录动作尝试；只有读回和目标生成的
   事件 ACK 才改变协作事实。
5. **不全机清理。** AEP 仅收束自己创建的 Job/worker；Qoder、Qoder CN、WorkBuddy、
   WorkBuddy AI、Codex 等宿主的 stdio MCP 归宿主管理。
6. **许可证前置。** 第三方源码、模型权重、DLL、浏览器驱动、AGPL 组件和云服务均不得
   因“可用”自动捆绑发行。每次安装/发布应写入来源、revision、license、NOTICE、运行时
   隔离路径和卸载方式。

## 6. 后续采纳路线

| 优先级 | 工作 | 成功条件 |
| --- | --- | --- |
| P0 | Qoder、Qoder CN、WorkBuddy、WorkBuddy AI、CodeBuddy 的原会话路线校准 | 每家保存唯一 anchor，完成无害 challenge 的 ACK |
| P0 | 将已探测桌面 Provider 升级为一次受控写入验收 | 形成 `INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK` receipt |
| P1 | Browser Use/Playwright/CDP Connector | 唯一 tab/target、只读回表、截图/网络/工件证据 |
| P1 | Open Interpreter/OpenHands 工程 Connector | 命令退出码、仓库 diff、工件 hash、交接快照 |
| P2 | UI-TARS/UFO/OmniParser 视觉 Provider 比较 | 在固定任务集上优于现有 UIA/OCR，且能解释失败 |
| P2 | OpenAdapt 回放 | 人类演示一次后，在正确会话锚点中可重复通过 |
| P3 | Temporal/A2A 生产互操作 | 跨进程/重启任务接力，保留 AEP 事件与审计边界 |

## 7. 关联资料

- [v0.6.35 交接](coordination/HANDOFF_v0.6.35_20261115.zh-CN.md)
- [桌面主控与无头增强验收](acceptance/DESKTOP_PRIMARY_HEADLESS_ACCEPTANCE_20261029.md)
- [第三方执行生态策略](THIRD_PARTY_EXECUTION_ECOSYSTEM.zh-CN.md)
- [开源能力补缺调研](OPEN_SOURCE_GAP_RESEARCH_20261029.zh-CN.md)
- [Cua Driver 接入与许可边界](CUA_DRIVER_INTEGRATION.zh-CN.md)
- [Windows 自动化适配器调研](WINDOWS_AUTOMATION_ADAPTERS.zh-CN.md)
- [Mage VL COWORK 评估](MAGE_VL_COWORK_ASSESSMENT_20261031.zh-CN.md)

本文件应在新增 Provider、安装/卸载运行时、完成真实闭环验收、引入可再分发依赖或改变
默认执行链时同步更新。
