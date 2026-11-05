---
name: aep-legion-announce
description: 在全队共享时间线发一条自定义通报；固定 room=agent-legion、默认只读预览、回执只认 DEPOSITED，不碰桌面不伪造送达。
min_agent_capability: cli
model_required: false
---

# aep-legion-announce

把「协作区通报」这件反复手作的事固化成弱 Agent 也能照做的确定流程。
它**只写一条共享日志事件**，不唤醒、不打字、不新建会话、不启动常驻进程。

## 何时用

- 有新 Agent 加入、有里程碑、有需要全队知晓的口径变更，要在协作区公告；
- 上一位 Agent 用错 room 把通报发进 `workspace`，需要按正确规则重发；
- 需要一份能抄进回执的通报证据（event_id、文本 SHA-256、目标名单）。

**不适用于**：派工、唤醒、插话、催收回表、判断对方是否接单、计算额度积分、
自动改源码、注册 OS 计划任务。

## 前置条件

- 有 `coordination` 运行时目录；不需要模型、浏览器、桌面窗口或 LM Studio。
- 通报正文写在 UTF-8 文本文件里，发起方名字是合法 Agent 名
  （`[a-z][a-z0-9_-]{0,63}`，默认 `workbuddyai`）。
- 知道三个既有坑（见下），尤其是 **`coord-chat-publish` 默认 room 是 `workspace`**。

## 步骤

1. **只读预览**（默认，零写入）：

   ```text
   python -m agent_evolution_protocol.coordination.legion_announce --coord-root coordination --source workbuddyai --text-file NOTICE_FILE
   ```

   或 `scripts/announce-to-legion.ps1 -TextFile NOTICE_FILE`。
   抄回执：`applied=false`、`room=agent-legion`、`writes_performed=0`、
   `plan.text_sha256`、`plan.mailbox_targets`、`plan.mailbox_commands`。

2. **按回执判定表判断**。`room` 不是 `agent-legion` 就停；目标名单缺人就在
   `plan.mailbox_targets` 里核对，不要自己硬编码名单。

3. **确认后才发布**（唯一写入口，只追加 1 条事件）：

   ```text
   python -m agent_evolution_protocol.coordination.legion_announce --coord-root coordination --source workbuddyai --text-file NOTICE_FILE --apply
   ```

4. **验证新事件**：不要只看 `coord-chat-read --limit N`。它是**按游标正序翻页**，
   返回 `after` 之后最早的 N 条，刚发的通报通常不在前几条。用
   `conversation_journal.read_events(root, after='<当日零时事件 id>', limit=500)`
   或直接读 `private_conversations/events/<event_id>.json`。

5. **可选：同步各 Agent 信箱**。按 `plan.mailbox_commands` 逐条执行既有
   `coord-send --topic legion-announcement --kind context --mailbox-only`
   （把大写占位符 `COORD_ROOT / SOURCE / NOTICE_FILE` 替换掉）。
   `--mailbox-only` 是关键：不加时 `coord-send` 默认可能向桌面打字。
   本能力**不自己写信箱**：`coord-send` 的消息格式是既有标准，复制它就是第二套标准。

6. **按回执口径收尾**：只宣称「已落盘、全队可读」，不宣称送达/已读/ACK。
   只有 `VERIFIED` 能关单。

## 回执判定表

| 回执字段 | 结论 |
|---|---|
| `applied=false` / `writes_performed=0` | 只读预览，**没有写任何文件** |
| `room=agent-legion` | 通报落在全队共享时间线（45+ 条既有事件同 room） |
| `room=workspace` | 发错房间，全队不会在时间线看到；停止并修正 |
| `verdict=DEPOSITED` | 事件已落盘；**不是送达、不是接单** |
| `delivered=false` / `acknowledged=false` | 本能力不投递、不收 ACK，恒为 false |
| `mailbox_sync=not_performed` | 信箱同步是独立步骤，按 `plan.mailbox_commands` 另做 |
| `plan.text_sha256` | 通报正文摘要，用于核对发出去的与批准的一致 |
| `plan.mailbox_targets` | 从 `coordination/messages/*/` 目录动态推导，不是硬编码名单 |
| `DEPOSITED` / `WINDOW_FOUND` / `INPUT_SUBMITTED` / `MESSAGE_VISIBLE` | 都不算送达 |
| `AGENT_ACK` / `WORKING` / `EVIDENCE_RETURNED` | 接单证据，仍需目标自己的 token |
| `VERIFIED` | **唯一**允许关单的结论 |

## 失败处置

- **禁止**用 `command_memory.announce()` 发自定义通报：它的正文和
  `bridge.AGENTS` 名单（7 个）都是硬编码，覆盖不到新 Agent；
  `bridge.agent()` 白名单还会直接拒绝新名字。
- **禁止**用 `team_dispatch.create()` 发通报：那是一对一派工，会触发唤醒并产生待 ACK 回执。
- **禁止**用 `transcript_bus.publish()`：它会把正文当 UIA 抓取增量清洗。
- **禁止**把「`coord-chat-read` 没看到」当成「没发出去」——先按步骤 4 用正确方式验证。
- 通报正文若包含未经核验的事实，必须写明信息来源（例如「据操作员通报，未经协议核验」），
  不要把它写成已验证的在线证据。
- 失败只允许三选一：**停止 / 延期 / 升级给人类**。

## 验收门

```text
python -m pytest -q tests/test_legion_announce.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。三个坑的完整实测与设计边界见
`docs/LEGION_ANNOUNCE.zh-CN.md`。
