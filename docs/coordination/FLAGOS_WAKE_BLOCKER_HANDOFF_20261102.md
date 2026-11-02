# FlagOS Agent 唤醒失败交接报告

核验时间：2026-11-02 15:38–15:40 CST。执行者：Codex。

## 结论

当前无法证明已经通过 AEP 调动任何目标 Agent。信箱落盘正常；原会话投递有明确失败和延迟回执；本轮没有目标 Agent 的回复 ACK，更没有任务执行结果。CLI 退出码 0 只表示返回了结构化结果。

用户已授权发送及唤醒，授权不是此次阻塞原因。此前 Feishu 的“love的工作伙伴”是另一个智能体，不能据此证明 WorkBuddy/Qoder 被唤醒；其两条发送记录还带红色失败标记。该路径不能作为军团投递验收。

## 实测 1：Qoder

命令（仓库根目录，PowerShell 7）：

```powershell
$env:PYTHONPATH='K:\PythonProjects5\agent-evolution-protocol\src'
python -m agent_evolution_protocol coord-send --from codex --to qoder --topic FLAGOS-WAKE-AEP-20261102-QODER --kind warning --text '用户授权使用 AEP 唤醒原会话。请读取 K:/PythonProjects5/FlagGems-sglang/competition/coordination/_out/msg_codex_opponent_playbook_20261102.md，报告当前任务和一条可核验进展，并回复 AEP-ACK:FLAGOS-WAKE-AEP-20261102-QODER。当前额度需重新读取后才能提交。' --dispatch
```

- 消息 ID：`20261102T073800053004Z-4971a758`
- 订单 ID：`2026-11-02T0738010285190000-f95f6aa7`
- 状态：`FAILED`
- 原始回执：`coordination/commander/state/2026-11-02T0738010285190000-f95f6aa7.json`
- 原因：`DISPATCH_REFUSED:one_event_id: found none, expected [20261102T073800053004Z-4971a758]`
- 校验发生在桌面输入之前；该次拒绝不能证明 UI 通道不可用，因为尚未进入该阶段。

源码证据：

1. `src/agent_evolution_protocol/cli.py:646` 将 mailbox 的 `mid` 直接作为 `event_id`。
2. `src/agent_evolution_protocol/coordination/commander.py:136` 仅在未传 event_id 时产生 `EVT-...`；正文追加了 `[AEP-ACK:20261102T073800053004Z-4971a758]`。
3. `src/agent_evolution_protocol/coordination/dispatch_gate.py:46` 的 EVENT 正则只匹配 `EVT-` 前缀。
4. 同文件第 156 行要求提取到恰好一个 event 且等于订单 event_id。因此时间戳形式的 mailbox ID 永远不能通过该门。

建议修复：明确区分 mailbox ID 与 canonical event ID，保存映射，并在创建订单时一次性固定正文及 SHA；或统一所有入口支持的 ID 契约。不要删除 one_event_id 检查、关闭校验、手改既有订单正文或伪造 ACK。添加从 coord-send 到 dispatch gate 的集成测试，覆盖 timestamp mailbox ID、EVT ID、重复和不匹配标记。仅正则单测不足以覆盖此次衔接错误。

## 实测 2：WorkBuddy3

- 调用同上，将 target 改为 `workbuddy3`，topic 为 `FLAGOS-WAKE-AEP-20261102-WB3`。
- 消息 ID：`20261102T073812672375Z-871cd060`
- 订单 ID：`2026-11-02T0738130411170000-f45d41ca`
- 状态：`DEFERRED`
- 原始回执：`coordination/commander/state/2026-11-02T0738130411170000-f45d41ca.json`
- 原因：`UNREGISTERED_CLIENT: register a verified client profile before delivery`
- 备用计划：`NO_QUALIFIED_PROVIDER`、`execution_started=false`。

配置观察：clients 目录有 `workbuddy_ai.json`，其 agent 为 `workbuddy_ai`，窗口标题为 `WorkBuddy AI`；没有据此证明其等于竞赛 bridge 的 `workbuddy3`。现有 ui_profiles 有 qoder、qoder_cn、codebuddy_cn、chatgpt，未观察到 workbuddy3 档案。必须实际确认进程、窗口、工作区、原会话和当前模型，才能建立绑定，不能直接复制或改名档案。

## 其他边界

- FlagGems-sglang 的 bridge.py 与 AEP coordination 是两个协作根。本轮 AEP 消息在 AEP 根，指令中的工件位于 FlagGems 根；需要明确跨根路由及回执回流，不能假定同名 Agent 自动共享信箱。
- 之前 FlagGems bridge 向 11 个身份落盘，仅证明文件投递。随后未观察到新回复；这不能推出全部代理离线或永久不能被唤醒。
- 本轮仅实投 Qoder、WorkBuddy3，未逐个验收其余客户端。
- 不应在没有活跃冲突证据时替其他 Agent 伪写 BLOCKED、释放其 ownership 或声称它们认可方案。

## 接手后的验收顺序

1. 修复 ID 契约并通过入口集成测试，保留原失败订单及回执。
2. 读取 `docs/coordination/URGENT_DELIVERY.md`、`HUMAN_UI_FALLBACK.md`、`DESKTOP_MCP_PROVIDERS.md`，确认真实原会话及全局输入租约。
3. 对 Qoder 使用新的、唯一事件执行一次有界投递。核验 INPUT_SUBMITTED / MESSAGE_VISIBLE，再等可归属于目标回复的挑战 ACK。遇到 UNKNOWN 不重放。
4. 对 WorkBuddy3 先完成真实客户端绑定，再显式恢复 DEFERRED 订单；检查是否还会碰到上述 ID 错误。
5. 要求对方做一个可核验的只读任务并落盘结果，最后读取 TASK_RESULT_READBACK。只有完成此链路才能报告“已调动”。
6. 确认 AEP 回复能回流到竞赛协作区，才扩大到全队。每个身份单独验收，不以某一通道成功代表全部成功。

未完成项：源码修复、客户端绑定、真实 ACK、任务执行回读。本报告是阻塞交接，不是完成证明。
