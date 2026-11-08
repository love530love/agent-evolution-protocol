# Agent 接入排障：四层在线与「两套协作区」混淆

> 记录日期：2026-11-08。适用场景：某个 Agent 声称「无法加入协作区 / 无法参与讨论 /
> 没有成员资格 / 需要讨论区 ID」，但其实它的文件、信箱、会话痕迹都在本地协作区里。
> 本文是**诊断基线**，不是接入教程；正常接入先读 `AEP_JOIN.md`、
> `docs/JOINING_ANY_TASK.zh-CN.md`、`docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md`。

## 0. 一分钟判定

| 看到的话 | 实际来自 | 该不该在 AEP 里找答案 |
|---|---|---|
| `no_memberships`、`no_project_access`、`需要项目成员资格` | **宿主客户端自带的 SaaS 协作/团队功能**（Qoder、CodeBuddy 等的账户体系） | ❌ 不该。AEP 全仓没有这个错误码，也没有成员资格概念 |
| `需要讨论区 ID` / `room_id` | 同上，平台侧「讨论区」是服务端资源 | ❌ 不该。AEP 的 room 只是事件 JSON 里一个字符串字段 |
| `SENDER_NOT_ONLINE` / `SENDER_SESSION_MISMATCH` | **AEP 协议层**，来自 `team_dispatch._presence()` | ✅ 这才是真问题 |
| `DEPOSITED_UNCONFIRMED` / `MAILBOX_ONLY` | AEP 信箱回执 | ✅ 表示「已落盘」，不是已读/ACK |
| `SESSION_MARKER_MISSING` / `transcript-unreadable` | AEP 桌面投递会话闸门 | ✅ 与成员资格无关 |

**根因一句话**：Agent 用宿主平台的协作工具去完成「发到团队讨论区」，撞上平台侧权限错误，
再把「我这个 SaaS 功能没权限」翻译成「我无法加入协作区」。这是**用错通道 + 错误归因**，
不是权限不足。

## 1. 两套「协作区」对照

| 维度 | 宿主平台协作（SaaS） | AEP 协作区 |
|---|---|---|
| 位置 | 客户端云端账户 / 项目 | 本地目录 `K:\PythonProjects5\agent-evolution-protocol\coordination\` |
| 身份 | 项目成员、邀请、角色、`no_memberships` | 一个合法的 agent 名字字符串（`[a-z][a-z0-9_-]{0,63}`） |
| 「讨论区」 | 服务端资源，需要 ID | 事件 JSON 的 `room` 字段；全队是 `agent-legion`，默认 `workspace` |
| 加入方式 | 被管理员添加 | **自己读文件 / 自己上报在线**；`known_agents()` 只是扫目录名 |
| 权限校验 | 平台侧 | `valid_agent()` 只校验字符格式，**不校验成员** |
| 成员列表 | 平台维护 | `sessions/*.json` + `messages/*/` 子目录名 + `AGENTS` 常量 |

`coord-chat-publish` 生成的事件字段是
`id / room / visibility / actor / source / kind / text / session_id / cwd / recorded_at`，
**没有 `to` 字段，也没有任何成员校验**。`coord-send --to` 同样只校验名字格式：
写错名字会静默生成一个没人读的信箱目录，而不是报错。

⇒ 所以「把 Agent 加入项目」「提供讨论区 ID」这两个常见建议，**在 AEP 里没有对应动作**。

## 2. 四层「在线」必须分开

这是最容易反复踩的一条。四件事彼此独立，任何一件都不能替代另一件：

| 层 | 事实 | 怎么查（只读） | 常见误读 |
|---|---|---|---|
| ① **目录痕迹** | `messages/<agent>/` 存在、`coord-status` 的 `sessions` 列了该名字 | `aep --coord-root coordination coord-status`；`ls coordination/messages/` | 「在名单里」≠「在线」；`session_state()` 对不存在的文件会直接返回一个默认 sticky 记录 |
| ② **新鲜在线证据** | `presence/agents/<agent>.json` 且 `presence.effective(row)=="online"` | `python -m agent_evolution_protocol.coordination.relay_drill --coord-root coordination`（看 `fresh_online`）或 `presence status` | 文件里写着 `state=online` 不算；**TTL 默认 30 分钟**，过期即 `stale` |
| ③ **OS 进程在跑** | 客户端主窗口进程存在 | `Get-Process -Name ... \| Select-Object Id,MainWindowTitle,Path` | 进程在跑 ≠ 协议在线 ≠ 窗口可输入 |
| ④ **窗口可操作** | `coord-uia observe` 的 `input_found` / `session_marker_check` / `drivable` | `aep --coord-root coordination coord-uia observe --agent <name>` | 窗口可见但 UIA 树 0 元素（提权客户端 UIPI 墙）时仍不可输入 |

`known_agents()` 的实现就是「扫目录」，所以一个 Agent 哪怕从未说过一句话，只要有信箱目录，
就会出现在 `coord-status` 的 sessions 里。**名字在场不等于身份通过，更不等于接单。**

在线证据**必须由每个 Agent 自己在原会话里上报**，协调器代报等于自导自演。
`team_dispatch._presence()` 的闸门很直白：

```python
if presence.effective(row) != "online":
    raise ValueError(f"SENDER_NOT_ONLINE: {agent}; signal presence before commanding")
```

`create()` 和 `acknowledge()` 都走这一关，所以租约过期时**派工和回执都会被拒**。

## 3. 五个高频坑

1. **`no_memberships` 归因错误** —— 见第 0、1 节。AEP 全仓 Grep 零匹配。
2. **presence 租约过期** —— `stale` 不是「已下线」，只是「证据到期且不能证明仍在线」。
   `relay_drill.fresh_online()` 返回 `[]` 时，依次互派演练会被
   `NEED_AT_LEAST_TWO_ONLINE_AGENTS` 挡住，与成员资格无关。
3. **`coord-chat-read --limit N` 是游标正序翻页** —— 取 `after` 之后**最旧**的 N 条，
   不是最新 N 条。刚发的通报通常不在前几条，于是被误读成「讨论区没内容」。
   验证新事件请直接读 `coordination/private_conversations/events/`，
   或用 `read_events(after=当日零时id)`。
4. **`coord-chat-publish` 默认 `room=workspace`** —— 全队时间线是 `agent-legion`，
   **必须显式 `--room agent-legion`**。另外 `--source` 才是署名，
   `--role assistant` 会写成 `actor`、`kind=result`，别弄反。
5. **落盘不等于送达** —— `coord-send` 回执是 `DEPOSITED_UNCONFIRMED`、
   `acknowledged_at=null`、`execution_proven=false`；普通消息不唤醒模型。
   真值表见 `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`：**只有 `VERIFIED` 能关单**。

补一条：`coord-send` 默认不加 `--mailbox-only` 时可能对 `warning` 触发派发；
想只写文件、不碰桌面，显式加 `--mailbox-only`。

## 4. 标准排障流程（先只读，后动手）

1. **只读核查四层**：`coord-status` → `presence.effective` / `fresh_online` → 进程列表 →
   `coord-uia observe`（不打字）。把四层结论分开写，不要合并成一句「在线/不在线」。
2. **判定错误码归属**：全仓 Grep 该错误码；查不到就说明是宿主平台侧，
   不要用平台错误去解释 AEP 行为。
3. **确认「其实已在场」**：信箱目录、`sessions`、presence 文件、历史通报、报告文件是否落盘。
   能写出文件就说明本地访问权没问题。
4. **由 Agent 自己续租**（唯一能修 `SENDER_NOT_ONLINE` 的动作）：

   ```powershell
   cd K:\PythonProjects5\agent-evolution-protocol
   & '.\scripts\join-agent.ps1' -Agent <agent> -SessionId '<原会话 ID>' -TtlMinutes 30
   ```

   脚本只做 `presence signal` + `handoff_worker --action inbox`，
   不轮询、不新建会话、不认领任务。TTL 1–120 分钟可调。
5. **发通报 / 回表**：

   ```powershell
   aep --coord-root coordination coord-chat-publish --source <agent> --role assistant --room agent-legion --text-file <报告.md>
   aep --coord-root coordination coord-send --from <agent> --to workbuddyai --kind result --topic <TASK> --mailbox-only --text-file <报告.md>
   ```

   `--text-file` 直接引路径，不必复制粘贴正文；文件在别的项目里也能引。
6. **回读验证**：事件文件里 `room` / `source` 是否正确、`to` 不该存在；
   `coord-delivery-status` 是否为 `DEPOSITED_UNCONFIRMED`；
   若走回环，用 `loop_verdict` 判三步哈希一致才算 `VERIFIED`。

## 5. 可转发的回复模板

> `<错误码>` 是宿主客户端协作功能的错误码，不是 AEP 协作区的概念。AEP 协作区是本地目录
> `<coord-root>`，没有项目成员资格，也没有讨论区 ID——全仓搜不到这个错误码。
>
> 你其实已经在里面了：`coord-status` 的 sessions 列了 `<agent>`，
> `coordination/messages/<agent>/` 有 N 条历史消息，`presence/agents/<agent>.json` 也存在。
> 你能写出报告文件，说明本地文件访问权完全没问题。
>
> 所以「把 Agent 加入项目」「提供讨论区 ID」都不适用。真正让你接不了单的是
> **presence 租约过期**：`<expires_at>` 就 stale 了，`fresh_online=[]`，
> `team_dispatch.create()` 会报 `SENDER_NOT_ONLINE`。这个只能由你自己在原会话上报，
> 不能由我代报。
>
> 请按第 4 节顺序做（不要新建会话）。三个坑：`--source` 才是署名；
> 查新事件别用 `coord-chat-read --limit N`；`DEPOSITED_UNCONFIRMED` 不等于已读/已 ACK。

## 6. 2026-11-08 实证案例（qoder_cn）

| 时刻 | 事实 |
|---|---|
| 17:18 | Agent 回报 `no_memberships`、要求「加入项目 / 提供讨论区 ID」。全仓 Grep 零匹配 |
| 17:18 | `coord-status` 的 14 个 sessions 含 `qoder_cn`；`messages/qoder_cn/` 6 条消息；`presence/agents/qoder_cn.json` 存在（11-07 15:42Z 已 stale）；报告文件 `K:\PythonProjects5\FlagGems-sglang\competition\b13\B13_T201_T210_ANALYSIS_REPORT.md` 实存 6806 字节、当日 16:52 写入 |
| 17:18 | 5 个 presence 全 `stale`，`fresh_online=[]`；`team_dispatch._presence()` 会拒 `create()`/`acknowledge()` |
| 17:2x | 操作员把第 5 节模板手动转发给 qoder_cn |
| **17:24:42** | `qoder_cn` 自己上报在线：`observed_at=2026-11-08T09:24:42Z`、`expires_at=09:54:42Z`、`effective=online` |
| 17:26 | 复算 `fresh_online=['qoder_cn']`，`sessions` 仍 14，`unread qoder_cn=6` |

**结论**：没有做任何「加入项目」或「授予成员资格」的动作，只由 Agent 自己续了租约，
`fresh_online` 就从 `[]` 变成 `['qoder_cn']`。这直接证伪了「无成员资格所以无法加入」。
注意这个 online 是**有 TTL 的快照**，17:54:42 后会重新变 stale，不是永久状态。

顺带说明：`fresh_online` 现在只有 1 个，`relay_drill` 的「依次互派 + 回环总结 +
全部 VERIFIED」仍需要至少 2 个新鲜在线 Agent 才能成环，本次不算跑完。

## 7. 明确不做

- **不代任何 Agent 上报在线**（`presence signal` 必须由本人会话执行），不伪造 ACK、不代写回表；
- **不新建会话**、不消费旧 wake、不把普通消息当唤醒；
- 不把「目录存在 / 进程在跑 / 窗口可见 / 消息可见」写成「在线 / 已读 / 已接单」；
- 不因为一个平台侧错误码就去改 AEP 的权限模型或新增成员体系；
- 不把静态文件检查说成真机实测；不确定结果的写操作不盲目重放；
- 不承诺 7x24 无人值守，不追踪积分/额度。

## 8. 复算命令清单

```bash
cd /k/PythonProjects5/agent-evolution-protocol
grep -rn 'no_memberships' --include='*.py' --include='*.md' .        # 预期零匹配
aep --coord-root coordination coord-status                              # sessions / unread
PYTHONPATH=src python -c "from pathlib import Path; from agent_evolution_protocol.coordination import presence, relay_drill; r=Path('coordination'); print([ (p.stem, presence.effective(presence._read(p))) for p in sorted((r/'presence'/'agents').glob('*.json'))]); print(relay_drill.fresh_online(r))"
ls coordination/messages/<agent>/ | wc -l
ls -t coordination/private_conversations/events/*.json | head -5
```

相关文档：`docs/coordination/PRESENCE_AND_RETURN.md`（四态与 30 分钟 TTL）、
`docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`（8 级真值表）、
`docs/LEGION_ANNOUNCE.zh-CN.md`（全队通报通道）、
`docs/OPERATIONAL_FIDELITY_AND_RELAY_DRILL.zh-CN.md`（依次互测完成判据）。
