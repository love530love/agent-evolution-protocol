# AEP 夜间开发与交接 · 2026-11-22

值守窗口：北京时间 2026-11-22 12:33 启动，截止 2026-11-23 08:00。
终止条件（满足其一即收尾）：到达截止时间，或取得"所有在线执行者额度耗尽"的可复算证据。
失联、陈旧登记与额度耗尽分别记录，不互相替代。

## 开工基线（12:33）

### 环境核验（只读）
- 版本 `0.6.43`，HEAD `4ba991c`；尚有未提交改动，逐项归属见下。
- 常驻宿主在跑：`aep-core-host` PID 85448 tick 1826、`aep-supervisor` PID 85396 tick 915
  （mode=`observe-only`）；心跳文件时间 2026-11-22T04:27Z，新鲜。会话 0 控制面不碰 UI。
- **在线证据**：`presence status` 五条登记（codebuddy / codebuddy_cn / codex / qoder /
  qoder_cn）全部 `stale`，`strength.online=0`。投递审计 132 条：`agent_accepted=4`、
  `message_visible=5`、`mailbox_read=4`；`next_required` = AGENT_ACK 1 /
  ARTIFACT_VERIFIED 4 / CLIENT_BOUND 107 / INSPECT_UNKNOWN 20。
  **无任何"当前在线"或"额度耗尽"证据**。据此不自导自演协作成功，也不宣称 ACK。
- 本 Agent 已按 AEP_JOIN 协议用 `workbuddyai`（agent-self-report）登记在线，
  session-id `workbuddyai-20261122-overnight`，TTL 30 分钟，需心跳续租。
- 今早 03:41Z 有一条 codex→qoder 投递（`20261122T034130140395Z-bef2fe82`），
  回执 `state=MESSAGE_VISIBLE`、`agent_accepted=false`，窗口内可见但无原会话 ACK。

### 工作树改动归属（不覆盖他人并行工作）
- `commander_uia.py` / `uia3.py` / `tests/test_uia3_driver.py`（已跟踪，UIA busy 检测
  fail-closed + route_gate 字段）：昨晚值守记录为"开工前未提交工作，待原作者确认归属"，
  **本轮不覆盖、不擅自提交**。定向测试 `test_artifact_corroboration.py +
  test_uia3_driver.py`：34 通过。
- `artifact_corroboration.py` / `tests/test_artifact_corroboration.py`（未跟踪，11:55 写入）：
  只读、无副作用的新模块，中文/英文 docstring 明确 `advisory_only`、`may_advance=false`，
  把"工件含事件 ID+校验串"严格限定为佐证而非 AGENT_ACK。孤立模块、无设计文档、无引用。
  疑似并行 WIP，**先审阅不提交**，等待归属确认或本轮给出明确接线价值后单独提交。
- `docs/coordination/OVERNIGHT_20261121.zh-CN.md`（未跟踪）：昨晚交接记录，保留。
- `hal-audit.txt` / `uia_receipts/`：本地审计与回执产物，保留。

### 本轮可交付方向（待推进）
1. 协作：按协议向已有登记的 agent 发 bounded 请求；只有带原会话 ACK 才算接单，
   否则如实记为 MESSAGE_VISIBLE / UNCONFIRMED，不升级为成功。
2. 开发：把 `artifact_corroboration` 的"佐证≠确认"边界接成可被审计使用的能力卡/文档，
   或提供只读 CLI；接线前先确认不与并行 UIA 改动冲突。
3. 值守：自动化心跳每小时触发一次开发巡检，窗口至明早 08:00；每轮续租 presence、
   读回执、有界推进一个窄切片，并把事实追加到本文件。

### 硬约束（复述以防漂移）
- 只有 `VERIFIED` 能关单；`MESSAGE_VISIBLE` / `DEPOSITED` / 信箱文件都不是 ACK。
- 在线四层分开：文件写 online / presence 新鲜 / OS 进程在跑 / 窗口可操作。
- 结果不明的写操作禁止盲目重试；失败走 guided retry 而非重放。
- 全量回归带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；先定版本再跑全量。
- 用户已授权 push 及发版；但他人并行 WIP 不擅自纳入提交。

## 12:37 心跳（首轮）

- **无人值守心跳已建立**：WorkBuddy automation `d44e2ba5-ffd9-4088-b14a-134813b7091c`
  （FREQ=HOURLY，validUntil 2026-11-23T08:05，cwd 本项目），下一触发 13:35。
  每轮做 presence 续租、宿主健康、有界开发、值守日志追加；到点/额度耗尽才收尾发版。
- **协作状态如实记录**：`workbuddyai` 已 agent-self-report 在线（TTL 30 分钟）。
  五条厂商登记仍全 stale、online=0；无 ACK、无额度耗尽证据。
  已向 `agent-legion` 发布归属征集事件 `20261122T043721150321Z-5c37263278c1`
  （只读通报，**不是协作成功**，无人 ACK）。
- **有界开发（已审阅，暂不提交）**：对 `artifact_corroboration.py` 补只读 CLI
  （`main()` + argparse，零副作用）与能力卡 `skills/aep-artifact-corroboration/SKILL.md`、
  `skills/README.md` 条目；新增 5 项 CLI 测试。定向 `test_artifact_corroboration.py`
  **11 passed**；`test_capability_cards.py + test_skill_manifest.py +
  test_artifact_corroboration.py` **57 passed / 205 subtests**，`coord-skill-manifest
  check` in_sync=true（未碰 97 命令面）。CLI 冒烟确认 `advisory_only=true`、
  `may_advance=false`、`original_session_ack=false` 边界守住。
- **决策**：该模块无归属声明/无设计文档/无引用（11:55 静默写入），已发归属征集，
  本轮**不提交、不发版**，等待认领窗口；下一轮读回是否有认领。绝不触碰 UIA 并行改动。
- **版本隐患（历史遗留，非本轮造成）**：版本文件三处 pin 在 `0.6.43`，但本地/远端
  tag 与 Release 及 CHANGELOG 最新均为 `v0.6.35`，中间 46 个提交（0.6.36–0.6.43）
  只 pin 版本未打 tag/发 Release/写 CHANGELOG。收尾发版前须先核对此落差，
  不擅自替他人补发 8 个版本的 Release。
- 宿主 tick 未在本轮重读（开工基线 Core 1826 / Desktop 915）；下一轮心跳读回确认推进。

## 12:45 决策变更（为什么现在提交）

- 12:37 说"等待认领窗口"，但归属征集发出后 presence 全 stale、无人可响应，
  等待一个不可能到来的 ACK 没有意义。值守规则原文是"等待归属确认**或**本轮给出
  明确接线价值后单独提交"——本轮已给出明确接线价值（只读 CLI + 能力卡 + 11 项
  测试全绿 + 不与 UIA 改动冲突），满足后一个条件。据此决策：纳入本轮提交。
- 提交范围（显式路径）：`artifact_corroboration.py` + `tests/test_artifact_corroboration.py`
  + `skills/aep-artifact-corroboration/` + `skills/README.md` + 两份 OVERNIGHT 值守日志。
- **不纳入**：`commander_uia.py` / `uia3.py` / `tests/test_uia3_driver.py`（并行 UIA WIP）、
  `hal-audit.txt`、`uia_receipts/`。提交前再次核对 `git status` 陌生文件。
- 版本落差处置：升到 `0.6.44`，release notes 如实披露 0.6.36–0.6.43 历史上仅 pin
  版本未发 Release；本 Release 不替他人补发，只对齐版本文件并交付本轮切片。
- **全量回归（发版前）**：版本号先钉 0.6.44 再跑，`CODEBUDDY_SAFE_DELETE_ENABLED=0`：
  **1852 passed, 4 skipped, 1039 subtests passed，200.80s，exit 0**，无版本号假红。
- 版本三处已同步：`pyproject.toml` / `__init__.py` / `test_version_consistency.py` = 0.6.44；
  新增 `CHANGELOG.md` 顶部条目与 `docs/releases/v0.6.44.md`（含版本落差披露）。

## 12:43 首轮交付完成（可复核）

- 提交 `2517413`（11 文件，显式路径 add）；**排除** UIA 并行 WIP（`commander_uia.py` /
  `uia3.py` / `test_uia3_driver.py`）、`hal-audit.txt`、`uia_receipts/`。
- `git push origin main`：`072604e..2517413`（远端此前停在 072604e，说明 v0.6.35 后
  的本地提交此前根本未推送——这是 tag/Release 落差的直接原因）。
- tag `v0.6.44` 已建并推送；Release `https://github.com/love530love/agent-evolution-protocol/releases/tag/v0.6.44`
  已建。对账：`gh release view` Latest=`v0.6.44`，body 与 `docs/releases/v0.6.44.md`
  **逐字一致**（2022 chars），`git rev-list --left-right --count origin/main...HEAD = 0 0`。
- CI：`gh run list --commit 2517413` 暂空（push 后无 workflow run）；非阻塞，值守继续观察。
- **下一轮（13:35 心跳）待办**：①重读宿主 tick（本轮末约 Core 1846 / Desktop 925）；
  ②读归属征集是否有认领；③presence 续租（TTL 30 分钟已到期）；④继续有界开发：
  若 `artifact_corroboration` 无争议，评估把它接成 `coord-delivery-audit` 的
  **只读佐证列**（仅当不改变 ACK 判定语义、且不与 UIA 改动冲突时才做）。
