# 全队通报：三个实测坑与固化边界

记录日期：2026-11-05（v0.6.23）

## 1. 起因

操作员要求「在协作区通报 claude 加入」。手工执行时发现：项目里**没有**专门的
全队广播命令（`docs/COORDINATOR_VISION_EVIDENCE.zh-CN.md:71` 早已把
「统一 `broadcast` / `notify_all` API」列为「完全没有实现」），而散落的三个既有入口
各有各的坑。本版把这条路径固化成 `coordination/legion_announce.py`，
**只写一条共享日志事件**，并如实说明哪些事它不做。

## 2. 三个实测坑（不是推测）

### 坑 1：`coord-chat-publish` 默认 room 是 `workspace`

`cli.py:3475` 的 `--room` 默认 `workspace`。全队共享时间线用的是
`agent-legion`（`team_dispatch.py:185/235`、`presence.py:120` 都用这个 room，
`private_conversations/events/` 里 45 条既有事件全部是 `agent-legion`）。
2026-11-05 hal 的审计通报就发进了 `room=workspace`，只有在遍历 events 目录时
才被看到。⇒ 通报必须**显式** `--room agent-legion`。

另外 `coord_chat_publish()` 把 `--role` 写成事件的 `actor`（`assistant`），
把 `kind` 固定为 `message`（role=user）或 `result`（role=assistant），
`--source` 才是真正的发起者。新模块直接调 `conversation_journal.append_event()`，
把 `actor` 与 `source` 都设为发起者、`kind="message"`，语义不再被 CLI 映射扭曲。

### 坑 2：`coord-chat-read --limit N` 是按游标正序翻页

`conversation_journal.read_events()`（`conversation_journal.py:62-81`）把事件按文件名
排序，跳过 `path.stem <= after` 的项，取**最早**的 `limit` 条就 `break`；
`coord_chat_read()`（`cli.py:1206`）默认 `after = chat_cursor_for(agent)`。
所以 `--limit 2` 返回的是该 Agent 游标之后最旧的两条（实测是 2026-10-29 的事件），
刚发布的 2026-11-05 通报根本不在结果里，看起来像「没发出去」。

正确验证方式：`conversation_journal.read_events(root, after='<当日零时事件 id>', limit=500)`
或直接读 `private_conversations/events/<event_id>.json`。
新模块把这段 `read_guidance` 写进每一次回执。

### 坑 3：`command_memory.announce()` 不能发自定义通报

`command_memory.py:264` 的正文是硬编码的 Windows 知识 context，
`event_id` 由三份固定文档的字节哈希生成（因此天然幂等），
收件人遍历 `bridge.AGENTS` 硬编码 7 个（无 claude），
注释明写 "Mailbox delivery is not read or ACK"。
`bridge.agent()` 是严格白名单，会直接拒绝 `claude`；
而 `cli.valid_agent()` 只校验字符格式、`known_agents()` 动态发现目录名。
⇒ 要发自定义通报且覆盖新 Agent，不能用它。

## 3. 设计：只写一条，不造第二套消息格式

```text
plan()  →  [仅 --apply] conversation_journal.append_event(room="agent-legion")  →  DEPOSITED
```

- `plan()` 只读：固定 `room=agent-legion`、正文 SHA-256、从
  `coordination/messages/*/` **动态推导**收件名单、生成 `coord-send --mailbox-only`
  命令清单（大写占位符 `COORD_ROOT / SOURCE / NOTICE_FILE` 需替换）。
- `--apply` 是唯一写入口，最多追加 **1 条**事件；重复调用是另一条事件（append-only，
  不做静默去重），不会变成常驻任务。
- **不自己写信箱**：`coord-send` 的消息格式是既有标准，复制过来就是第二套标准
  （项目记忆里的硬规则）。信箱同步作为独立步骤，按命令清单执行。
- 不新增 `coord-*` 命令面（用模块入口 + `scripts/announce-to-legion.ps1`），
  不新增 schema，不改 mission 文件，不碰桌面。

## 4. 回执与自动边界

| 字段 | 含义 |
|---|---|
| `applied` / `writes_performed` | 是否真写、写了几条（0 或 1） |
| `room` | 恒 `agent-legion`；出现 `workspace` 即错误 |
| `verdict` | `NOT_DEPOSITED`（预览）/ `DEPOSITED`（已落盘） |
| `delivered=false` / `acknowledged=false` | 本能力不投递、不收 ACK，恒假 |
| `mailbox_sync=not_performed` | 信箱同步需另做 |
| `read_guidance` / `announce_trap` | 坑 2 与坑 3 的原文提示，随回执一起交付 |
| `models_loaded=0` / `processes_started=0` / `ui_touched=false` / `deployment_performed=false` / `system_tasks_registered=false` | 硬边界，任一变真即越界 |

| 动作 | 是否自动 |
|---|---|
| 只读预览、推导收件名单、给出信箱命令清单 | ✅（默认） |
| `--apply` 追加一条全队共享事件 | ✅（需显式开关） |
| 投递到桌面 / 唤醒模型 / 新建会话 | ❌ |
| 收 ACK、判定接单、关单 | ❌ **只有 `VERIFIED` 能关单** |
| 写信箱消息 | ❌ 交由既有 `coord-send --mailbox-only` |

`DEPOSITED / WINDOW_FOUND / INPUT_SUBMITTED / MESSAGE_VISIBLE` 不是送达；
普通消息不唤醒模型，不新建会话（默认 `forbid-by-default`）。

## 5. 2026-11-05 手工通报的实测留痕（本能力的用例）

| 项 | 结果 |
|---|---|
| 全队共享日志 | `20261105T090213167489Z-a7898427b8c9`，`room=agent-legion`、`visibility=workspace` |
| 信箱通报 | 13/13 成功（`coord-send --kind context --mailbox-only`），含新建的 `messages/claude/` |
| 回执 | `coord-delivery-status --to claude` = `DEPOSITED_UNCONFIRMED`、`acknowledged_at=null`、`execution_proven=false` |
| 桌面 | 13 条消息都**没有** `desktop_delivery` 字段 ⇒ 没有触发任何打字 |
| 口径 | 通报正文写明「据操作员通报，未经协议核验」，未宣称在线/已读/ACK |

## 6. 明确未做

- 不实现「统一 broadcast API」的 CLI 命令（刻意不加 `coord-*` 命令面，
  避免命令面漂移与第四套 envelope）；
- 不把通报变成 wake，不自动催收回表，不自动判定接单；
- 不追踪积分/额度/成本，不部署外部运行时，不注册 OS 计划任务；
- 不承诺 7x24 无人值守，不宣称「代替人类工程师」；
- 触发脚本 `scripts/announce-to-legion.ps1` 是一次性有界脚本：纯 ASCII、
  不循环、不 sleep、不后台运行，默认不带 `--apply`。

## 7. 验收

`tests/test_legion_announce.py`：默认预览零写入、room 恒 `agent-legion`、
收件名单动态推导且命令含 `--mailbox-only` 不含 `--dispatch`、空文本/非法名字拒绝、
`--apply` 只写 1 条事件且字段正确、回执写明两个读坑与 `DEPOSITED` 口径、
重复调用是不同事件但硬边界恒 0/false、AST 守线（无网络/进程/模型/`while`）、
不出现 `build_parser` / `add_parser`（不新增命令面）、能力卡与本文档存在、
触发脚本纯 ASCII 且默认不 `--apply`。

全量回归结果见 `docs/releases/v0.6.23.md`。
