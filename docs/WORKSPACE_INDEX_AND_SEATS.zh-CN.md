# 工作区索引、席位与在线检测（2026-11-22）

> 目标：让协作区像「群聊天 + 索引目录」，任何加入者（含弱 Agent）都能读取关键信息，
> 知道谁加入了、谁离线但保留席位，并能主动查询在线状态。本文只描述**现有代码**
> 已实现的能力与边界，不声称未落地的自动化。

## 1. 一条只读命令看全协作区

```
.tools/agent-runtime/Scripts/aep.exe coord-workspace-index <view> --coord-root coordination
```

| view | 回答的问题 | 数据来源（既有标准，未新建 schema） |
| --- | --- | --- |
| `overview` | 加入后第一眼该看什么 | 聚合下面全部视图 |
| `members` | 谁有席位、谁在线、谁离线但席位保留 | `presence.snapshot` + sessions/ + messages/ + mailbox/ |
| `newcomers` | 最近 N 小时哪些身份有加入通知 | `presence/notices/*.json` 的 `joined` 事件 |
| `chat` | 群里说过什么 | `conversation_journal.read_events`（游标正序翻页） |
| `knowledge` | 我们已知什么（受控只读查询） | `knowledge_store.query`（SQLite `mode=ro`） |
| `roles` | 有哪些分工角色 | `role_contract.ROLES`（功能名，不绑定厂商） |
| `health` | 谁在线、谁离线检测 | `collab_health.snapshot`（四层在线） |

**硬边界**：全部只读。读取不会创建 SQLite 文件（缺库返回 `STORE_ABSENT`），
不会打字、不会投递、不会标记 ACK。可见性 ≠ 接单，只有 `VERIFIED` / `AGENT_ACK` 关单。

## 2. 席位：离线也保留

席位来自三个身份来源的并集：`presence/agents/*.json`、会话绑定 `sessions/*.json`、
 mailbox 目录。一个 Agent 即使 presence 过期，**席位仍保留**（报告层保留窗口 7 天），
状态记为 `stale` 或 `offline`，不会被从目录里删除。

- `seat_kept=true`：协作区里仍有它的位置，可被唤醒；
- 过期超过 7 天：`seat_kept=false`，但历史群聊与知识记录仍在；
- 在线人数 `online` 只反映「evidence 新鲜」，不等于已接单。

席位与新陈代谢由 `coord-presence signal/reconcile`（各 Agent 自报）与
`next-due`（下一次续租截止时间）维护；本索引层只读取，不代替自报。

## 3. 新成员发现

`newcomers --within-hours 24` 列出本协调目录中最早可读的 `joined` 通知位于窗口内的身份，
并给出通知来源和证据路径。心跳续租或 `rejoined` 不会把已有身份重新算作新人。
历史通知缺失时，实际加入时间未知；不等于没有加入。通知只证明在这个协调目录观察到
presence 变化，**不是带 workspace_id 的正式成员登记**，也不证明该 Agent 已读消息或接单。

## 4. 在线主动查询 + 离线检测

`health` 视图复用 `collab_health` 的四层在线：

1. `file_registered_online`：文件登记为在线的席位；
2. `presence_effective_fresh`：evidence 新鲜的在线（stale 单列，不并入在线）；
3. `os_processes_running`：OS 进程在跑（无可靠只读来源时为 `unknown`）；
4. `windows_actionable`：窗口可操作（同样可能 `unknown`）。

判定纪律（fail-closed）：过期的 presence 记 `stale`，**绝不降为 offline**；
心跳里的 pid 不是活进程的证明；`unknown` 不算在线也不算离线。
`verdict=READY` 只说明「有新鲜证据且宿主健康」，**不是投递成功或 ACK**。

这套检测在常驻机构（`succession_board` 态势板、`coord-coalition capacity-status`）
中已被消费；托盘常驻脚本（右下角图标区、无黑窗）负责宿主存活与心跳，
托盘图标或心跳本身都不代表某个 Agent 已接单。

## 5. 群聊式事件流

`chat` 视图通过既有 `conversation_journal` 读取 `agent-legion` 等房间的事件，
支持 `--after <cursor>` 正序续读、`--limit` 限量（1–500）。事件可见只表示消息落盘，
不表示已读或已 ACK。全队通报用 `aep legion-announce`（默认 dry-run、只写一条共享事件）。

共享协调目录可能包含多个工作区。带水印的群聊可通过主命令按 `workspace_id` 精确过滤；
下面每条都是独立的单行 PowerShell 命令，其中 `W-aaaaaaaaaaaaaaaa` 必须替换成实际工作区 ID：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol coord-workspace-index chat --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa' --room 'agent-legion' --contains '进展' --limit 50
```

`next_cursor` 可通过 `--after` 继续读取；过滤时未标记 workspace_id 的历史事件不会混入。
未传 `--workspace-id` 的结果标记为 `coordination-root-unfiltered`，不能视作已隔离的单项目
群聊。工作区**名称**不等于形如 `W-...` 的工作区 ID。上述筛选已接入主命令，但尚未专门接入 MCP；
只检索显式发布到 journal 的消息，不抓取全部
厂商的私人会话。

## 6. 受控知识查询（SQL 风格的边界）

`knowledge` 只接受固定参数过滤：`--kind / --actor / --task-id / --contains / --limit`。
**调用方的 SQL 不会到达 sqlite**，从根上杜绝注入与误写；数据库以 `mode=ro` 打开。
`limit` 超 500 直接拒绝。这与「提供 SQL 查询能力」并不矛盾：能力以结构化、
可审计的方式暴露，而不是把裸 SQL 交给任意写入者。

## 7. 能力卡与验收

- 能力卡：`skills/aep-workspace-index/SKILL.md`（六节结构、回执判定表、失败处置）。
- 守线：`tests/test_workspace_index.py`（席位保留、新成员、参数化查询、健康视图、
  只读不建库）、`tests/test_capability_cards.py`、`tests/test_skill_manifest.py`。

```
.tools/agent-runtime/Scripts/python.exe -m pytest -q \
  tests/test_workspace_index.py tests/test_capability_cards.py tests/test_skill_manifest.py
```
