# 接手态势板 · 额度受限下的指挥权接力视图

> 状态：已实现、14 项定向测试通过；用 2026-11-22 真实协作区冒烟，读数如下文。
> 本模块是**组合视图**，不是第二套标准：所有判定复用既有模块，自身只读、零写入。

## 1. 为什么需要这一层

多厂商 agent 同时开工时，每个模块都只回答自己的一小问：

| 已有模块 | 回答的问题 |
|---|---|
| `coordination/capability_roster.py` | 谁声明了什么能力（短 TTL 自声明） |
| `coordination/legion_ledger.py` | 谁做过什么贡献（激励/记分） |
| `coordination/collab_health.py` | 舰队是否还活着、宿主 tick 是否推进 |
| `federation/command_handoff.py` | 哪个功能角色由谁持有、能否接管 |
| `relay/resources.py` | 谁的额度信封还 ACTIVE、可承担什么能力 |
| `coordination/presence.py` | 四层在线中的 presence 证据是否新鲜 |

但一个刚接手的弱 agent 真正要问的是：**现在谁在指挥、哪个角色空着、我能不能接、
缺什么证据**。此前没有一条命令能回答。于是出现一种假象：每个人都在写代码，
但指挥链其实没有被声明、没有可接手者。本模块把上述事实聚合成一张可复算的板。

## 2. 契约与边界

- 只读：不写文件、不起进程、不联网、不加载模型；`build()` 对固定 `at` 完全确定。
- 复用判定：presence 用 `presence.effective()`；健康用 `collab_health.snapshot()`；
  角色用 `command_handoff.status()`；额度用 `relay.resources.snapshot()`。
  任一来源不可读 ⇒ `verdict=DEGRADED` + `SOURCE_UNAVAILABLE:*`，不猜。
- 不新增 schema 文件（`schemas/` 仍为 7 个），不注册新 `coord-*` 命令，
  `cli.py` 与 `skill-manifest` 均不动，避免与并行 agent 的写入范围撞车。
- 建议恒 `advisory_only=true / may_advance=false / execution_started=false /
  requires_human_review=true`。`dry_run_command` 只是投递预览，不是角色认领、不是 ACK。
- stale 保持 `stale`（证据过期），不是 `offline`；但 stale 不能作为接手依据。
- 消息文本里出现 `AEP-ACK` 也不改 `message_is_ack=false`。

## 3. 用法

```text
python -m agent_evolution_protocol.coordination.succession_board \
  --coord-root coordination --at 2026-11-22T06:00:00+00:00

python -m agent_evolution_protocol.coordination.succession_board \
  --coord-root coordination --format brief
```

输出字段：`verdict` / `roles[]` / `agents[]` / `claims[]` / `gaps[]` /
`takeover_advice[]` / `health_verdict` / `note`。能力卡：
`skills/aep-succession-board/SKILL.md`。

## 4. 2026-11-22 真实读数（14:02 CST / 06:02Z）

用真实 `coordination/` 冒烟，得到：

- `verdict=GAPS`；4 个功能角色（commander / scout / github / builder）
  **全部 `UNCOVERED / STANDBY / holder=None`**；
- `takeover_advice=0`：没有任何 agent 同时满足 fresh presence + ACTIVE 额度 +
  能力 token 匹配；
- 28 个命名缺口，其中关键项为：`NO_CAPABILITY_DECLARATION`、
  `ACK_PENDING:1`、`ROLE_NOT_HELD:*`、`QUOTA_UNDECLARED:*`、
  `NO_FRESH_PRESENCE:*`；
- presence：`workbuddyai` 是唯一 fresh online，其余厂商 stale 或未登记；
- 真实 claim：copilot-github / qoder / qoder_cn / workbuddy 均有有效 claim，
  但 claim ≠ 角色 holder，不能直接当作指挥权。

**结论**：跨厂商开发在真实发生（OS 进程、UIA busy、代码提交均可复算），
但「额度受限时任意 agent 可接手」的**证据链尚未闭合**——缺的不是第三个状态机，
而是各 agent 用既有 API 声明 `capability` 与 `quota`、由 holder 显式执行 handoff。
本模块把这个缺口变成一条可复算读数，避免用「大家都在干活」冒充「指挥链可接手」。

## 5. 让缺口闭合的最小动作（由各 agent 自己做，我不代做）

1. 每个在线 agent 用 `relay` 的 `declare` 子命令声明额度信封（含 `capabilities`），
   不要自报成厂商余额证明；
2. 用 `capability_roster.declare(...)` 声明能力 token 与证据（短 TTL 到期重报）；
3. 当前 holder 通过 `command_handoff` / `relay` 的授权流程显式 yield/accept，
   接手后回读状态确认 single ACTIVE holder；
4. 再跑本板：`verdict` 应从 `GAPS` 变为 `ACTIONABLE`，并出现具体接手建议；
5. 任何投递都要保留原会话 ACK 证据；`MESSAGE_VISIBLE` 与 mailbox 文件不算。

## 6. 测试

`tests/test_succession_board.py` 14 项，覆盖：空舰队命名缺口、三闸门同时满足才有建议、
stale presence 不 qualify、EXHAUSTED / UNDECLARED 额度阻断、能力 token 必须匹配、
holder 不推荐自己、过期 claim 忽略、畸形 paths 字符串容错、消息不算 ACK、
零写入、固定时刻确定性、来源损坏 DEGRADED、CLI JSON/brief 双格式。
