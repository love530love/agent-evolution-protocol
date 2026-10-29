# 信箱优先与情报闭环

> 目的：让协作成员在不启动无意义模型轮询的前提下，及时看到高价值通报，并把“看到、接单、产出、回传”与桌面唤醒严格分开。

## 1. 运行规则

每个 Agent（包括主调度 Codex）在加入工作区、恢复长任务、收到归队通知或开始新一轮作战前，必须先执行一次信箱分诊：

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\join-agent.ps1 `
  -Agent <自己的稳定 ID>
```

脚本会使用 AEP 的 canonical bridge 根目录；若直接调用 Python，`--root` 是
`K:\PythonProjects5\agent-evolution-protocol\coordination`，而 `--bridge-root` 是
`K:\PythonProjects5\agent-evolution-protocol\src\agent_evolution_protocol\coordination`，不能把两者混为同一路径。

分诊结果按 `urgent → 最近截止时间 → event_id` 排序。`urgent` 或已逾期的事项标记为 `requires_immediate_read=true`，必须先读、先 ACK/拒绝，再开始普通工作。

这只是按需读取，不是常驻轮询；静默时不启动模型、不打开窗口、不消耗额度。事件监听器只负责在新文件到达时提醒，完整内容仍以持久信箱为准。

## 2. 证据分层

| 状态 | 能证明什么 | 不能证明什么 |
|---|---|---|
| `MAILBOX_ONLY` / 文件存在 | 指令已持久化，可恢复 | 对方已读、已接单、开始执行 |
| `MESSAGE_VISIBLE` | 原会话出现了消息且输入框状态有证据 | 模型理解、任务采纳 |
| `AGENT_ACK` | 指定 Agent 用同一事件 ID 回执，并可附分析产物哈希 | 产物正确、外部任务成功 |
| `EVIDENCE_RETURNED` | 产物和日志已回传 | 产物通过独立复核 |
| `VERIFIED` | 独立门禁/回表确认 | 下一轮无需重新评估 |

桌面 UI 回执中的 `task_delivered`、窗口聚焦或输入框清空，不能覆盖信箱/派发回执中的 ACK 状态。若二者矛盾，保守采用“已见但未接单”，继续等待同事件 ID 的 Agent ACK。

## 3. 高优先级情报策略

1. 重要通报必须带 `event_id`、`priority`、`reply_due_at`、证据路径和下一动作。
2. Agent 读到 urgent 事件后，先写同事件 ID 的 ACK/拒绝；若无法立即执行，说明原因、预计恢复时间和可接力范围。
3. ACK 不等于完成。只有分析文件、测试日志、哈希或外部回表齐全，才允许推进阶段。
4. 逾期事项不静默重发：先核对原会话、租约和副作用，再走显式重试或改派。
5. 同一故障签名连续出现时，暂停同族投递，发布“冻结/修复/解冻”通报，避免额度和窗口被重复消耗。

## 4. 与无人值守任务的关系

任务监督器只响应事件：新信箱文件、客户端回执、阶段变化、截止时间、回表变化或用户指令。没有变化时不发送消息、不唤醒模型。机器重启后先恢复信箱分诊，再恢复租约和 mission checkpoint；不能把旧 presence 当作当前接单证明。

## 5. 当前实战记录（Batch 12）

- Qoder CN：事件 `D20261029T141556795637Z-e4ca82a1bbba` 已有同事件 `AGENT_ACK`，产物为 `coordination/delegation/artifacts/.../qoder_cn.json`；结论是 T178 r1 缺少 Kunlun vendor 文件，且现有拆核体的运行时 K 循环与既有 Kunlun 约束冲突，下一实验应先做 constexpr K/static range。
- CodeBuddy CN：事件 `D20261029T141950539551Z-c70cd7b58efd` 已有同事件 `AGENT_ACK`，产物为 `coordination/delegation/artifacts/.../codebuddy_cn.json`；T182/T183 静态门和 CUDA 数值门均通过，但六颗非 NVIDIA 芯片仍未平台验证。
- 两次桌面回执仍分别保留 `agent_ack_verified=false` 或 `task_delivered=false` 字段。它们证明了 UI 发送链的观测差异，不能把 UI 成功点击升级为 Agent 接单。

## 6. 开发方向

### P0：已落地

- 信箱分诊返回 priority、截止时间、逾期和“立即查看”标记，并按紧急度稳定排序。
- 继续保持 durable mailbox、原事件 ID、原会话策略和 ACK/产物证据分层。
- 将“新消息到达”作为事件源，禁止静默模型轮询。

### P1：下一步

- 把 `handoff_worker inbox` 的分诊摘要接到 `aep mission status` 和桌面提示层。
- 为每个 Agent 维护 `last_inbox_read_at`、`last_urgent_ack_at` 和未读主题游标；只在游标变化时触发通知。
- 将派发回执、UI 回执、Agent ACK、产物回传统一映射到 delivery truth matrix。
- 建立单一 schema 注册表和 CI 校验，阻止文档与路由器各自发明版本名（历史复核已发现
  `aep-next-action-v1` 与 `agent-evolution-next-action-v1` 不一致）；消息、动作建议和消费端必须引用同一 canonical schema。

### P2：自进化

- 对“收到但未 ACK”“UI 可见但未采纳”“ACK 后无产物”“产物被独立门禁否决”建立失败签名，自动生成下一步动作。
- 用事件驱动的 watchdog/SSE/文件通知替代定时扫描；无 watchdog 时仅保留有界、可暂停的补偿扫描。
- 每轮结束生成情报摘要 checkpoint，供新 Agent 用一句命令恢复，而不是搬运全文聊天记录。

## 7. 验收标准

- 新 Agent 首次查看信箱即可看到 urgent 事项及截止时间；
- 普通事项不会遮蔽 urgent 事项；
- 文件投递、UI 可见、Agent ACK、产物和独立验证在报告中分别显示；
- 静默期间没有模型轮询；
- 旧记录和旧客户端仍可读取，不需要迁移或删除历史信箱。

