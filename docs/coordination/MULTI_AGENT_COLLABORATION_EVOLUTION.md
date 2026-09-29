# 多智能体通信与协作机制演进设计

> 状态：设计 / 交接文档  
> 范围：在现有本地协作总线之上渐进演进，不替换现有 mailbox、任务认领和审计机制。  
> 本文不授权实现变更；任何协议、权限或自动唤醒行为的代码改动都应先经项目负责人确认。

## 1. 执行摘要

建议把现有机制演进为“**本地协作总线 + 按客户端能力接入的适配器 + 可选标准协议接口**”，而不是引入一个新的多智能体编排框架取代它。现有文件 mailbox / ack / claim 机制和不可变审计记录继续作为兼容入口与审计来源；在其上补齐统一事件信封、明确任务状态、幂等投递、租约与重试，再按实际可用的客户端接口逐个接入。

职责边界：

| 组件 | 负责什么 | 不负责什么 |
|---|---|---|
| 本地协作总线 | 消息持久化、任务/线程关联、认领与交接、审计和投递状态 | 代替客户端的模型运行时、身份认证或工具配置 |
| Hook / CLI / 客户端适配器 | 将特定客户端生命周期事件映射到总线；在明确支持时请求唤醒 | 假定所有桌面应用都能访问或控制彼此的私有会话 |
| MCP | 向客户端提供一致的总线工具接口 | 定义任务编排、所有客户端的在线状态或自动唤醒 |
| ACP | 对明确实现 ACP 的编码客户端提供可选会话/运行时互操作 | 强迫不支持 ACP 的客户端迁移或绕过其运行时 |
| A2A | 未来需要跨主机、跨组织代理互操作时的远程协议候选 | 本地单机 mailbox 的必要依赖 |

**关键限制**：共享本地总线只能通过目标客户端实际提供的 hook、插件、SDK、MCP/API 或人工入口与其交互。它不能通用地读取其他应用的私有聊天、写入其会话，或唤醒任意桌面应用。未验证出受支持接入点的客户端必须标记为 `manual-only`，并提供清晰的人工收件箱路径。

## 2. 现状与缺口

以下现状依据项目现有协作设计和实现核对资料整理；这些是增量设计的基线，不代表本文建议修改当前行为。

| 现有面 | 当前能力 | 演进时需保留 / 注意 |
|---|---|---|
| `competition/coordination/README.md` | 定义协作协议、mailbox、ack、task claim、提示词/上下文同步、审计证据、隐私护栏、有限自动唤醒；保留旧记录 | 它是现有协议的兼容基线；新协议应定义与现有记录的映射和渐进读取策略 |
| `bridge.py` | 使用原子 JSON 文件承载 mailbox、acks、claims；支持 `send/inbox/ack/claim/release/watch`；登记 codex、workbuddy、qoder、codebuddy、github_copilot；可尽力镜像到 realtime hub | 文件总线仍是兼容入口和审计来源；镜像失败不能伪装成发送成功或破坏本地记录 |
| `realtime_hub.py` | localhost HTTP 服务、SSE 事件流、追加式事件文件、事件校验和带认证 POST；只对 codex/workbuddy 实现显式 worker wake；接受 qoder/github_copilot 作为事件 actor；空闲 hub 不轮询模型 | 接受事件不等于能唤醒对应客户端；保留 localhost 限制和显式唤醒语义 |
| `realtime_hook.py` | `--agent` 当前仅有 codex/workbuddy；项目范围内的 prompt/result hooks；仅识别精确 `@wake` 短语；需要本地 token 文件；不自动轮询模型 | 不应将 hook 的能力外推到 Qoder、Copilot 或其他客户端 |
| `comms/comms.py` / `comms/README.md` | CLI roster、timeline、board 和 send wrapper；roster 包含 codex、qoder、github_copilot、workbuddy、codebuddy | 将 CLI 作为通用人工入口和脚本适配器，不等于客户端原生集成 |
| 客户端接入 | Codex 在 `.codex/hooks.json` 配置 hook；WorkBuddy/Hermes 文档描述接入方式；Copilot/Qoder 可作为 bus actor，但当前核对未确认直接 hook 或自动唤醒实现 | 能发 bus 消息不意味着能嵌入当前客户端聊天或读写私有会话；先核实公开、受支持的接入点 |

### 当前主要缺口

1. 事件、消息、ack、claim 与运行状态还需统一标识和明确关联，避免重试时重复执行以及把“收到”误判为“完成”。
2. 文件原子写能提供基础本地可靠性，但多步交付、消费者崩溃、租约过期、重试上限和死信处理需形成一致语义。
3. roster 记录参与者，不等于可验证的 capability registry；“在线”“可唤醒”“支持哪些事件”应分开陈述。
4. SSE 应有稳定重放游标及客户端断线恢复策略；实时推送不能成为唯一消息保管处。
5. `/api/bootstrap` 当前会向 UI 引导响应提供 token。把这一点列为**高优先级安全审查项**：在确认 UI 的信任边界、存储方式和访问控制前，不应将 hub 暴露到 localhost 以外；本文不判断其已构成可利用漏洞，也不要求在此次文档任务中改代码。

## 3. GitHub 参考项目与筛选方法

### 方法与选择标准

本次以 GitHub 上的官方规范、上游源码和一手项目文档为依据，按以下标准选取参考：是否提供可直接落到现有 bus 的协议/生命周期/可靠性模式；边界是否清晰；是否能与现有 mailbox 渐进共存；以及是否有明确的安全或人工审批语义。这里是有界样本，不是全 GitHub 的 exhaustive 排名；搜索受限流影响，不能据此声称穷尽或按流行度排名。只因 star 数高、但与当前接入问题关系弱的项目不作为设计依据。

| 项目 / 一手资料 | 观察到的具体机制 | 对本项目可借鉴之处 | 不应照搬之处 |
|---|---|---|---|
| [MCP 规范：Transport](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/transports.mdx#L63-L84)、[SSE resumability](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/transports.mdx#L164-L189)；[TypeScript SDK 固定版本说明](https://github.com/modelcontextprotocol/typescript-sdk/blob/7f7a94c22017e121a960e071bb50ec75e34450bd/README.md#L36-L44) | Streamable HTTP 的本地传输安全建议包括 localhost 绑定、Origin 校验和 DNS rebinding 防护；SSE 可用 `Last-Event-ID` 恢复事件流 | 可在总线外增加 MCP server，向支持 MCP 的 agent 暴露 inbox/send/ack/claim 等工具；事件流借鉴游标重放 | MCP 是工具访问接口，不是任务队列、状态机或 agent 调度协议；不要求现有客户端必须使用 MCP |
| [ACP prompt-turn / session updates](https://github.com/agentclientprotocol/agent-client-protocol/blob/e04a8dbe11d59d7b34effe22b3c3f2f6fa5a0eeb/docs/protocol/v1/prompt-turn.mdx#L10-L29)、[取消语义](https://github.com/agentclientprotocol/agent-client-protocol/blob/e04a8dbe11d59d7b34effe22b3c3f2f6fa5a0eeb/docs/protocol/v1/prompt-turn.mdx#L334-L361)、[tool call states / permissions](https://github.com/agentclientprotocol/agent-client-protocol/blob/e04a8dbe11d59d7b34effe22b3c3f2f6fa5a0eeb/docs/protocol/v1/tool-calls.mdx#L34-L36)；[Zed 外部 agent 集成](https://github.com/zed-industries/zed/blob/00def035dec61f4fafceecc4aceb6f44f2c96f49/docs/src/ai/external-agents.md#L24-L36) 与[运行时边界](https://github.com/zed-industries/zed/blob/00def035dec61f4fafceecc4aceb6f44f2c96f49/docs/src/ai/external-agents.md#L123-L136) | ACP 区分 session 更新、message/tool/status/plan、取消与工具状态；Zed 展示了与多个外部编码 agent 协作的客户端集成方式，同时保留 agent 自身的运行时、认证和工具配置边界 | 若某编码客户端明确实现 ACP，可做可选适配器；将取消、权限提示和会话事件映射为总线事件 | ACP 是可选的客户端协议，不可推定 Qoder/Copilot/WorkBuddy 已实现；不应借接入绕过客户端自己的认证/工具权限 |
| [A2A 项目目标与异步/流式/推送](https://github.com/a2aproject/A2A/blob/main/README.md#L48-L50)、[能力范围](https://github.com/a2aproject/A2A/blob/main/README.md#L82-L88)、[治理](https://github.com/a2aproject/A2A/blob/main/README.md#L129) | 聚焦不同 agent 系统的发现、异步任务与流式/推送互操作，且有 Linux Foundation 治理 | 当且仅当需要跨机器、远程发现和跨系统消息互通时，再评估作为远程边界 | 本地协作不需要先改成 A2A；不要为单机引入网络发现、远程信任和额外部署面 |
| [OpenAI Codex hook 事件类型](https://github.com/openai/codex/blob/44fe510ce3ee61c8ef623adcbf89b901c73ddd61/codex-rs/hooks/src/lib.rs#L22-L34) | 源码定义 `UserPromptSubmit`、`PostToolUse`、`SessionStart/End`、`SubagentStart/Stop` 等生命周期 hook 事件 | 对已有 Codex hook 做更完整、幂等的生命周期映射；避免仅依赖自由文本约定 | 这是 Codex 的扩展点证据，不是其他客户端拥有相同 hook/API 的证据；事件仍需最小化和用户授权 |
| [LangGraph durable execution / HITL](https://github.com/langchain-ai/langgraph/blob/07b33185eab893be2ed031eedae52f09314bf77c/README.md#L37-L42) | 将持久执行、可恢复运行与 human-in-the-loop 作为图执行能力 | 借鉴显式保存运行状态、等待人工输入后恢复、避免长任务仅存在于内存 | 不以 LangGraph 替换 mailbox、bridge 或现有 claims；框架图执行不是本项目当前协作总线的缺口的最小解 |

**结论**：本地 bus 负责持久化和协调；hooks 是逐客户端适配器；MCP 是工具接口；ACP 是可选 coding-client 生命周期互操作；A2A 是未来远程互操作候选；持久工作流框架只提供模式参考。

## 4. 目标架构

```text
Codex hooks ─┐
WorkBuddy ───┤       ┌─ MCP server（可选工具入口）
Qoder ───────┼─ adapter ─> bus API / versioned events ─> file mailbox + audit log
Copilot ─────┤       │                               ├─ SSE stream + replay cursor
CodeBuddy ───┘       └─ manual inbox / CLI            └─ claims / leases / delivery state
                                  │
                                  └─ optional ACP adapter; A2A only for remote need
```

1. **兼容核心**：保留 `bridge.py` 的 send/inbox/ack/claim/release 语义、现有 JSON 文件及历史审计记录；新代码先经兼容层读取/写入，不一次性切换存储格式。
2. **统一事件信封**：新事件使用版本化 envelope；旧记录读取时由兼容层补齐可推导字段，不应伪造无法推导的发送者、时序或执行结果。
3. **状态与交付分离**：同一任务的业务状态机独立于传输状态；消息被投递、被 ack、被 claim 和任务成功完成各有明确事件。
4. **适配器按能力启用**：每个客户端 adapter 只上报其真实支持的能力。adapter 缺席、断开或未认证时，消息留在 durable inbox 并可由 CLI/board 人工领取。
5. **观察层与存储分离**：SSE 用于低延迟通知，游标重放和持久记录用于恢复；UI、watcher 断线不能造成消息丢失。
6. **协议渐进增加**：先落实事件契约和 bus API，再提供 MCP 工具；ACP、A2A 分别以客户端明确支持和跨主机需求为启用条件。

### 4.1 版本化事件信封

建议新事件的最小字段如下；可按 `event_type` 约束字段组合，但不能省略事件唯一性、项目范围或时间信息。

```json
{
  "schema_version": "2.0",
  "event_id": "uuid",
  "room": "repo-or-collaboration-room",
  "project_id": "project-id",
  "thread_id": "conversation-or-work-thread-id",
  "task_id": "task-id",
  "parent_event_id": "previous-event-id-or-null",
  "correlation_id": "end-to-end-operation-id",
  "sender": { "agent_id": "codex", "adapter": "codex-hook" },
  "recipient": { "agent_id": "workbuddy" },
  "event_type": "task.message",
  "timestamp": "RFC3339-UTC",
  "expires_at": "RFC3339-UTC-or-null",
  "idempotency_key": "stable-key-for-retries",
  "content": {
    "text": "最小必要的任务摘要",
    "artifact_refs": [
      { "uri": "repo-relative-path-or-approved-artifact-uri", "sha256": "digest" }
    ]
  },
  "delivery": {
    "attempt": 1,
    "status": "queued",
    "wake_requested": false
  }
}
```

字段约定：

- `room` / `project_id` 决定授权边界；`thread_id` 串起讨论，`task_id` 串起可执行工作。不要把 room 名称当作授权凭证。
- `parent_event_id` 用于事件因果关系，`correlation_id` 用于同一操作跨适配器、重试和回执追踪。
- `idempotency_key` 在同一次逻辑发送的重试间保持稳定；新的逻辑消息必须生成新 key。
- `content` 优先放短摘要和受控 artifact 引用，避免复制整段会话。artifact 应校验项目范围、权限和内容摘要。
- `delivery` 表示交付尝试，不是任务结果；唤醒请求和实际执行也须独立记录。
- 禁止包含凭据、hub token、未脱敏日志、其他项目内容或与任务无关的私有会话全文。需要上下文时由发送者提供经过筛选的摘要或显式授权的引用。

### 4.2 状态机与语义

将**消息投递状态**与**任务执行状态**分开存储。一个建议的聚合任务状态序列是：

| 状态 | 含义 | 允许的主要后续状态 |
|---|---|---|
| `queued` | 总线已持久化，等待匹配接收方 | `delivered`、`expired`、`cancelled`、`failed` |
| `delivered` | 已放入接收方 inbox 或收到 adapter 投递回执 | `acknowledged`、`expired`、`cancelled`、`failed` |
| `acknowledged` | 接收方确认看到消息；不代表已接单或完成 | `claimed`、`needs_input`、`cancelled`、`expired` |
| `claimed` | 某个 agent 持有有效任务租约 | `running`、`needs_input`、`failed`、`cancelled`、租约过期后重新 `queued` |
| `running` | agent 已明确开始执行 | `needs_input`、`completed`、`failed`、`cancelled` |
| `needs_input` | 等待用户/负责人/其他 agent 的明确输入或审批 | 输入到达后恢复 `queued` / `claimed` / `running`，或 `cancelled` / `expired` |
| `completed` | agent 提交了结果和所需 artifact/证据回执 | 终态 |
| `failed` | 不可自动恢复的失败或重试耗尽；需错误原因和死信记录 | 经人工重开后创建新尝试/新任务 |
| `cancelled` | 取消请求已被执行端确认，或未运行任务已被总线终止 | 终态 |
| `expired` | 截止时间已过且任务未完成 | 经人工重新提交后创建新任务 |

实现时允许用单独的投递表表达一次任务的多个消息投递；上表的核心不变量必须保持：

- **ACK != completion**：ack 仅表示看到；claim 表示接单；`completed` 必须有执行结果。
- **wake requested != worker started != work done**：依次记录请求、adapter/worker 启动回执、结果事件。当前能力仅 codex/workbuddy 支持显式 wake，不能将该能力赋予所有 roster 成员。
- 任何终态都需带 `event_id`、`task_id`、actor 和时间；失败需包括可操作错误类别，避免只有“失败”。
- 取消是协作请求：运行中任务只有在执行端确认停止后才进入 `cancelled`；无法确认时保持执行态并上报取消待决，禁止伪报成功取消。

### 4.3 投递可靠性

- **幂等去重**：按 `project_id + sender + idempotency_key` 对逻辑发送去重；按 `event_id` 对事件消费去重。重复请求返回原回执，而不是新增重复工作。
- **Lease / heartbeat / reclaim**：claim 返回 `lease_id` 与 `lease_expires_at`；运行中 agent 周期性 heartbeat。租约过期后先检查最近状态/审计记录，再允许回收和重派；回收事件必须可审计。
- **有界重试**：只对可重试的传输/暂时错误退避重试；设最大次数和截止时间。永久校验/权限错误不重试。
- **死信**：重试耗尽、schema 无法解析或持续投递失败的事件进入可见 dead-letter 视图，保留原因、最后一次尝试和恢复操作；不静默丢弃。
- **一致性**：先将事件写入持久记录，再发 SSE 通知/请求 wake；后续步骤失败不得回滚已记录的事实，而要追加失败或待重试状态。
- **排序**：仅在同一 task/thread 的因果链内提供顺序保证；不要承诺不同任务间全局顺序。
- **审计**：状态更改追加新事件，不覆写既有审计事件；可变的当前状态是可由事件重建的投影。

### 4.4 参与者与能力注册

roster 和 capability registry 应分开概念。建议每个参与者公布类似字段：

```json
{
  "agent_id": "qoder",
  "project_id": "project-id",
  "adapter_type": "manual",
  "capabilities": ["cli", "inbox_read", "message_send"],
  "supported_events": ["task.message"],
  "presence": "manual-only",
  "wake_supported": false,
  "last_seen_at": null,
  "adapter_version": null
}
```

`adapter_type` 可取 `native_hook`、`MCP`、`ACP`、`CLI`、`manual`；presence 至少区分 `online`、`busy`、`manual-only`、`offline`、`unknown`。`wake_supported` 必须是布尔值且有实际 adapter/能力测试证据；“被 roster 登记”“能作为 bus actor 发消息”不可推导出在线或可自动唤醒。presence 需有时间戳和过期策略，不能将历史活动当作当前在线状态。

### 4.5 按客户端的接入矩阵

| 客户端 | 当前确认的接入 | 演进方向 | 明确限制 |
|---|---|---|---|
| Codex | repo 内 `.codex/hooks.json`；已有 `realtime_hook.py` prompt/result hook；当前只识别 codex/workbuddy 参数并支持显式 wake | 基于官方生命周期 hook，逐事件映射 prompt、tool、session/subagent start/stop；加入幂等 key、项目范围、可审计失败回执 | 不复制无关会话上下文；保留用户/仓库范围和 token 文件保护；仅显式请求才 wake |
| WorkBuddy / Hermes | 项目文档已有 repo-scoped hook/set up 描述；hub 对 workbuddy 有显式 wake | 补齐一致事件 envelope、投递确认、租约和失败报告；明确哪类事件可在用户授权后触发 wake | 不把系统级或跨项目上下文镜像到本项目 |
| Qoder | roster / bus 可识别 actor；未确认原生 hook 或自动 wake | 先核实产品提供的受支持 hook、MCP/API 或插件；没有则以 CLI/inbox 人工模式参与 | 不构造不存在的 live-chat embedding、私有会话读取或唤醒能力 |
| GitHub Copilot | 可登记为 bus actor；未确认本项目中有原生会话 hook / 自动 wake | 先验证当前产品实际提供且获准使用的扩展点；支持 MCP 时可通过工具访问 bus；否则 script/CLI/manual | 不声称 bus 可读写 Copilot 私有会话；MCP 工具存在也不代表能从 bus 强制唤醒 Copilot |
| CodeBuddy | roster 已列出；具体原生 hook / wake 能力未确认 | 作为 host/参与者先走 CLI/manual adapter；只有完成能力核验后再升级 | 当前不声明实时订阅或自动唤醒支持 |

**人工模式必须是一等模式**：`manual-only` 的任务仍应可在 CLI/board 中查看、ack、claim、回复和附上结果；UI 清楚显示“等待人工处理”，不能显示为在线、运行中或自动投递成功。

### 4.6 实时界面、MCP 与可选 ACP

- SSE 事件附单调递增 `event_cursor` 或稳定事件 ID；断连重连支持 `Last-Event-ID` 或等价游标，检测游标过期时返回明确的重新同步响应。
- SSE 仅负责通知。客户端重连后从持久记录补齐缺失事件；投影重建与消息消费必须幂等。
- MCP server 可将 `send_message`、`list_inbox`、`ack_message`、`claim_task`、`heartbeat`、`complete_task`、`request_approval`、`cancel_task` 包装为受限工具。所有工具按 project/agent ACL 授权；危险写操作要求明确 actor 身份和幂等 key。
- MCP server 不应向模型暴露 hub 凭据或任意文件系统；工具只接收项目范围 ID 和已授权 artifact URI。
- ACP adapter 只有在具体客户端提供并声明支持 ACP 时才运行，将 session/status/tool/plan/cancel 转换为总线事件；ACP session 身份与本地 agent 身份需显式绑定。
- A2A 仅在出现跨机器 agent 发现/调用需求后做单独威胁建模和方案评审。默认保持 localhost-only。

## 5. 安全、隐私与协作冲突

### 5.1 安全与隐私护栏

1. **网络边界**：服务默认只绑定 loopback；即使本地也校验 Host/Origin，拒绝不可信跨源请求并防 DNS rebinding。远程访问必须是单独批准的设计，不通过放宽绑定“临时解决”。
2. **凭据边界**：token 文件使用最小文件权限、可轮换、可撤销；日志不记录 token。UI bootstrap 目前包含 token 的行为列为 P0 审查：优先改为短时、限定 project/操作范围的 session credential 或同等安全方案，并避免向不可信页面提供 bearer secret；安全方案需评审后决定。
3. **身份与授权**：对 `agent_id`、project、room、task 做服务端 ACL；请求 body 中自报 actor 不能单独证明身份。按最小权限区分 read、send、ack、claim、wake、cancel、approve。
4. **输入约束**：校验 schema/version、event type、路径与 artifact 引用；限制请求体大小、字符串长度、嵌套深度和事件速率。拒绝绝对路径、路径穿越及未授权项目引用。
5. **隐私最小化**：只传播完成任务所需的摘要、差异或显式授权 artifact；禁止默认镜像所有 agent 会话、终端输出、用户提示词或其他项目工作区。
6. **用户控制**：唤醒、跨客户端写入、取消、审批和敏感 artifact 分享需遵守明确授权策略；保留选择 manual-only 的能力。
7. **可审计且不过度留存**：记录谁在何时对哪个 project/task 做了什么及其结果；定义敏感 payload 的脱敏和保留期限，审计记录不得变成未脱敏私聊副本。
8. **UI 安全**：UI 展示按项目授权过滤；不得把 hub token 渲染给页面脚本或浏览器存储。任何跨来源或远程 UI 需独立安全评审。

### 5.2 文件冲突与并行工作

- 每个 task 的 claim lease 指明执行者、范围、到期时间；**同一路径/同一逻辑任务原则上只有一个写者**。需要协作时，拆分互不重叠的 path ownership 或采用明确的 proposal/review，而非多人盲写。
- 推荐每个 agent 使用独立 worktree/分支；协调者负责指定变更 owner、审查集成结果和唯一 merge owner。不要让一个 agent 代替另一个 agent 修改其未授权工作区。
- 多 agent 同时提出的改动先作为带 task/thread/artifact SHA 的提案进入 review；审阅记录引用确切提交或内容摘要。
- 合并回执包含源 worktree/分支、变更范围、提交/patch SHA、reviewer 和合并结果。冲突解决由明确指定的 merge owner 完成。
- claim 路径语义继续沿用现有实现；新协议只补充 path-level ownership、lease 和审计，不另造互不兼容的 claim 机制。

## 6. 渐进迁移路线

每个阶段均需有 feature flag 或可回退边界；不得以迁移为由删除旧 mailbox/audit 数据。P0 是开始任何暴露面扩展前的门槛。

| 阶段 | 工作内容 | 退出条件 | 回退方式 |
|---|---|---|---|
| **P0 安全审计与能力盘点** | 审查 `/api/bootstrap` token 暴露、localhost/Origin/Host 校验、token 文件权限/轮换、payload 校验、project ACL；逐个验证 roster 客户端的 hook/MCP/ACP/API 能力和 wake 支持 | 有书面威胁模型、token/UI 决策、能力矩阵证据；未证实的能力统一标为 `manual-only`；风险未关闭前不开放远程访问 | 保持现有 localhost-only 和现有人工流程；禁用未验证 adapter/wake |
| **P1 v2 schema 与旧 JSON 兼容** | 定义版本化 envelope、事件类型、状态映射、校验器、幂等键；添加旧记录读兼容和新记录双读/影子解析 | 新旧格式测试通过；旧记录可查看；重复事件不重复生成新逻辑消息；未知版本明确拒绝或隔离 | feature flag 关闭 v2 写入；继续使用旧 JSON 写入，新格式仅保留兼容读取 |
| **P2 交付可靠性与 SQLite WAL / replay** | 在不移除 JSON 审计的前提下逐步引入当前状态投影/投递账本（可评估 SQLite WAL）；实现游标重放、lease/heartbeat/reclaim、有界重试和 dead-letter | 崩溃重启、重复投递、SSE 断线重放、租约回收均可验证；JSON 审计与新投影可对账；备份/恢复演练成功 | 停止新投影写入并从 JSON 审计重建；保留只读诊断，新功能关闭 |
| **P3 Codex / WorkBuddy adapters** | 为现有 hook 接入 v2 envelope、幂等回执、显式 wake / started / result 分层事件；完善人工降级 | 每种 hook 事件有契约与权限测试；wake 请求、worker 启动、执行结果可区分；断开时 inbox 可恢复 | 按 agent/事件关闭 adapter；回退到现有 bridge、hook 和人工 inbox |
| **P4 Qoder / Copilot 接入验证** | 分别验证其公开受支持的 hook/API/MCP 等入口；未证实之前提供 CLI/script/manual adapter 和清晰状态 | 每个客户端有可复现能力证据、用户授权和撤销路径；未支持时验收 manual-only 可用 | 移除 adapter 配置，不影响 bus actor 或其他 agent |
| **P5 MCP server / ACP 可选适配** | 提供带 ACL 的 bus tools；按实际客户端能力启用 ACP adapter；完善 Origin、Host、鉴权和工具权限 | MCP 客户端只能执行授权操作；取消/审批状态可观察；ACP 不支持时系统完全可用 | 独立关闭 MCP/ACP 服务；CLI 和 file mailbox 仍为入口 |
| **P6 A2A（条件性）** | 只有真实跨机器互操作需求出现后，设计远程身份、发现、授权、网络边界和协议转换 | 有独立需求批准、威胁模型、互操作测试和回滚演练 | 保持本地 bus；移除远程 adapter 不影响本地任务 |

SQLite WAL 是候选的状态/投递账本实现，不是必须立即进行的存储替换。JSON 追加审计记录先保留为可读证据；迁移前须定义一致性检查、重建与恢复流程。

## 7. 验收标准与故障场景

### 必须满足的验收条件

- 老版本 bridge 操作、现有 mailbox/ack/claim 文件和既有审计记录可继续读取；未知 `schema_version` 不会被静默接受。
- 同一 `idempotency_key` 在重复提交、超时重试和 adapter 重启下最多创建一份逻辑任务；重复 ack/完成事件不改变终态或生成二次副作用。
- ACK、claim、wake requested、worker started、completed 在 API、CLI、UI 和审计记录中语义一致且互不混淆。
- 消费者崩溃后租约最终过期并可安全回收；活跃 heartbeat 可阻止误回收；状态恢复操作有审计。
- SSE 客户端断线重连能按游标恢复；游标过期会清晰报告需要全量同步，而不是漏消息或静默重放不完整数据。
- 只有实际有受支持 adapter 的 agent 显示 `wake_supported=true`；Qoder/Copilot/CodeBuddy 默认 manual-only，除非能力验证有新证据。
- 未授权 project/task 的读、写、claim、wake、cancel 均被拒绝；跨源请求、超限 payload、路径穿越、凭据样式内容及未知 schema 按策略拒绝或脱敏。
- 人工 inbox/CLI 在 hub/SSE/MCP 停机时仍可用；bus 不轮询模型，不把应用在线状态伪装成工作进度。
- 并发路径 ownership 可检测冲突；每项并发变更有确定的写者和 merge owner；artifact SHA / receipt 可追溯。

### 需覆盖的失败场景

1. 写入持久事件后 hub/SSE 发送失败：消息仍可见并可重放，状态为待交付/可重试。
2. adapter 收到消息但回执丢失：重试得到原任务而不重复执行。
3. agent ack 后崩溃、claim 后断线或运行时 hang：分别按状态和 lease 规则恢复，不误报完成。
4. 两个 agent 同时 claim 或同一路径：只有一个有效 lease / writer；冲突被可见拒绝。
5. 任务执行中收到 cancel：记录取消请求，等待执行端确认；无法确认时不伪报已取消。
6. SSE cursor 无效、过期或事件文件不可读：明确错误和重同步流程，保留原始诊断信息。
7. token 过期/撤销、Origin 不匹配、项目 ACL 拒绝：明确授权失败，不进入无限重试。
8. schema 版本升级中断或 SQLite 投影与 JSON 审计不一致：检测对账失败并按迁移回滚步骤恢复。
9. 被截断/超大/恶意消息或 artifact 越权引用：在持久化和分发前拒绝，记录最小化审计元信息。
10. 未集成客户端收到新 task：CLI/board 显示 `manual-only` 待处理，而不是假装在线或自动唤醒。

### 运行观测指标

建议按 project/agent/adapter 聚合，不采集不必要的消息正文：

- queued/delivered/acknowledged/claimed/needs_input/completed/failed/expired/cancelled 数量及停留时间分布；
- 投递延迟、ack 延迟、执行时长、重试次数、幂等去重数、dead-letter 数量；
- 活跃租约、heartbeat 超时、reclaim 次数、误回收报告；
- SSE 重连数、游标恢复成功率、需要全量重同步次数；
- wake 请求、实际启动回执与结果回执的数量/比率，区分“请求”与“成功”；
- schema 拒绝、ACL/Origin 拒绝、payload 限制、凭据脱敏触发次数；
- JSON 审计与状态投影对账差异、恢复/回滚次数。

事件正文、用户提示词、token 和未经脱敏的私聊内容不得作为常规指标标签或日志字段。

## 8. 尚需负责人批准的决策

本文件只提出实现前需讨论的方向，不替负责人作出产品或安全决定：

1. `/api/bootstrap` token/UI 的目标认证设计与 P0 风险关闭标准。
2. v2 envelope 必需字段、事件保留期限、人工重新打开失败任务的操作语义。
3. path-level claim 的粒度、默认 lease/heartbeat/重试参数，以及是否采用 SQLite WAL 作为状态投影。
4. 对 Codex/WorkBuddy 自动唤醒的授权粒度和是否需要逐任务确认；其他客户端只在能力验证后单独批准。
5. 是否、何时为支持 MCP/ACP 的具体客户端部署额外 adapter；跨机器前是否确有 A2A 需求。
6. 用户批准后再拆分 P0-P6 实施任务；本文没有执行其中任何代码变更。

## 9. 本次交接范围

这是设计和交接文档：建议渐进增强已有 bus，而非以 LangGraph、CrewAI、AutoGen 或其他编排框架整体替换。实施者应从 P0 安全/能力盘点开始，保持 localhost-only、旧 JSON 与人工 inbox 可用，逐阶段验证并保留回滚开关。本次仅新增本文档，未修改实现代码，未运行测试或启动服务，也未提交或上传变更。
