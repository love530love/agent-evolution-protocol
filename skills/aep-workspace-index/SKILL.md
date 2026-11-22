---
name: aep-workspace-index
description: 只读共享索引——查席位目录、谁新加入、群聊事件、分工角色，以及对知识库做受控只读查询；让任意新加入的 Agent 一眼看清协作区。可见性不等于接单，只有 VERIFIED / AGENT_ACK 关单。零写入、零模型、零网络。
min_agent_capability: cli
model_required: false
---

# aep-workspace-index

协作区既是群聊，也是索引目录。新加入的 Agent（尤其是弱 Agent）最常问四件事：
**谁在场、谁来过、说过什么、我们知道什么**。本卡用一条只读命令把这四类事实
聚合出来，替代私下打听。**它不替代** `coord-presence`（写入席位）、
`coord-chat-publish`（发事件）、`coord-uia`（投递）或任何判定层：只读，不写协作区。

## 何时用

- 刚加入协作区，需要先看清全局再决定认领哪个角色；
- 想确认某人的席位是否保留（离线保留席位 ≠ 在线）；
- 想知道最近 24 小时有哪些新 Agent 报过到；
- 想按关键词 / 角色 / actor 查已有的决策与经验，而不是重新研究。

**不适用于**：发送消息、唤醒/插话、认领角色、写 ACK、改订单、注册 OS 任务或调用模型。

## 前置条件

- 站在仓库根目录，使用项目自带解释器 `.tools/agent-runtime/Scripts/python.exe`。
- 有 `coordination/` 目录即可；某来源缺失时对应视图返回空并说明原因，不猜测。
- 知识库 SQLite 默认取协作根目录下的 `knowledge.sqlite`，也可用 `--db` 指定。
- 不需要桌面窗口、管理员权限、网络、模型或 API Key。

## 步骤

1. **读一页式总览**（加入后第一件事）：

   ```text
   .tools/agent-runtime/Scripts/aep.exe coord-workspace-index overview --coord-root coordination
   ```

2. **抄下席位目录**：运行 `coord-workspace-index members --coord-root coordination`，
   记录回执的 `total`、`online`、`seats_kept` 三个字段。

3. **查新成员**：运行 `coord-workspace-index newcomers --within-hours 24 --coord-root coordination`，
   抄下 `joined[]` 与 `count`。

4. **读群聊**：运行 `coord-workspace-index chat --limit 50 --coord-root coordination`，
   抄下 `count` 与 `next_cursor`（下次用 `--after` 续读）。

5. **查知识**：运行
   `coord-workspace-index knowledge --contains DPI --limit 50 --coord-root coordination`，
   抄下 `available`、`count`、`rows[]`。参数全是固定过滤（kind/actor/task-id/contains），
   不接受调用方 SQL。

6. **读角色目录**：运行 `coord-workspace-index roles --coord-root coordination`，
   抄下 `roles[]`（functional name，不绑定厂商）。

7. **在线主动查询 / 离线检测**：运行 `coord-workspace-index health --coord-root coordination`，
   抄下 `verdict` 与 `online_layers`（四层在线：文件登记 / presence 新鲜 / OS 进程 / 窗口可操作）。

## 回执判定表

| 字段 | 取值 | 结论 | 下一步 |
| --- | --- | --- | --- |
| `total` / `online` | 整数 | 在场与在线席位计数 | 在线数只说明 evidence 新鲜，不可当接单 |
| `seats_kept` | ≥ `online` | 离线席位被保留 | 需要唤醒时另走 `aep-desktop-wake` |
| `joined[]` / `count` | 数组 / 整数 | 近期报到的 Agent | 只是首次证据时间，未知时间不算加入 |
| `next_cursor` | 事件 ID | 群聊续读游标 | 下次用 `--after <next_cursor>` 正序翻页 |
| `knowledge.available` | `true` | 知识库存在且可读 | 读 `rows[]` |
| `knowledge.available` | `false` + `reason: STORE_ABSENT` | 无知识库 | 属正常空索引；读不会创建文件 |
| `roles[]` | 含 commander/scout/github/builder | 角色是功能名 | 认领仍须走 `coord-claim` |
| `health.verdict` | `READY` | 有新鲜在线证据且宿主健康 | 仍非投递 / 非 ACK |
| `health.online_layers` | `*_unknown` | 该层无可靠只读来源 | 未知不算在线，也不算离线 |
| 任何视图 | 文本可见 | 仅可见 | 绝不升级为 ACK 或已接单 |

## 失败处置

- `STORE_ABSENT`：停止查询；这是空索引不是错误，不要创建数据库文件。
- 命令退出码非 0 / 参数被拒：修正参数（如 `--limit` 超过 500），不要绕过校验。
- 视图缺字段或 `schema` 不符：**停止**，升级给人类，不要据残缺回执下结论。
- 需要真正投递或认领：停止本卡，改用 `aep-desktop-wake` / `aep-start-dispatch`。

## 验收门

```text
.tools/agent-runtime/Scripts/python.exe -m pytest -q tests/test_workspace_index.py tests/test_skill_manifest.py
```

期望：`test_workspace_index.py` 全绿（索引只读、席位保留、新成员、参数化查询），
且 manifest 守线 `in_sync=true`、`count` 与文档一致。
