# WIP 归属台账（2026-11-23 08:25 CST）

## 为什么有这份台账

用户要求"把托盘/额度接手那批 WIP 的持有者拉起来确认归属后逐一入库"。本台账记录
**实际做过的认领动作与收到的证据**，不把"落盘通报"冒充"已读/接单/认领"。
与 `docs/HANDOFF_OWNERSHIP.zh-CN.md` 的纪律一致：claim + 有效租约才算认领，
未知保持未知，历史通报 ≠ 当前归属。

## 已发出的认领动作（可复核）

- 全队通报事件：`20261123T002103058064Z-7607ac0cd0cb`（room=agent-legion，source=workbuddyai）。
- 定向信箱（kind=question，`--mailbox-only`，落盘不唤醒）：
  `coordination/messages/{codex,workbuddy,qoder,qoder_cn,copilot-github}/20261123T0021*.json`。
- 请求按五组列出文件范围（A 托盘部署 / B 额度接手 / C 本地模型 / D UIA-coordination / E 运行时垃圾），
  要求持有者用带 event id 的 AEP-ACK 自报"已测试全绿/是否仍在写"。
- **结果**：截至 08:25，无任何 agent 对该请求回 AEP-ACK。按协议口径，**认领未发生**。

## 已收到的主动自报（非针对本请求，但构成归属证据）

- **codebuddy**，`20261123T002117451214Z-858cef65`（+ 同文投 copilot-github / github_copilot）：
  自报负责 `federation/capability_bridge.py`、`tests/test_handoff_capability_bridge.py`、
  `docs/HANDOFF_UNDER_QUOTA.zh-CN.md` 第 7 节；已自行提交 `3800e88` + `332c7f2`；
  明确声明"未改他人 WIP（托盘、core_host、uia3、presence 等未提交改动一律未触碰）"；
  并如实标注未落地项：自动额度采集、CLI 写状态、授权签名、赛事端到端演练。
  → 这部分**已入库、归属清楚**，不需要我再动。
- **Codex** 审计 `docs/AUDIT_COLLAB_HANDOFF_20261122_CODEX.md`：明确说托盘/UIA/coordination
  那批未提交改动属"持有 UIA/coordination 未提交修改的队友"，需要与它逐块整合；
  并列出三个隔离 worktree 原型（`7aaf139` / `4711a20` / `cb4b470`），均未合入主树。
  → 这部分**仍无认领**。

## 待认领 WIP 分组（当前一律不代提交）

| 组 | 文件 | 测试状态 | 归属 | 动作 |
| --- | --- | --- | --- | --- |
| A 托盘/部署 | `start-aep-tray.cmd/.vbs`, `restart-aep-tray.cmd`, `scripts/aep-tray.ps1+strings.json`, `install-aep-service.cmd`, `scripts/{install,uninstall,bootstrap-elevate,deploy-once}-*.ps1`, `deploy.cmd`, `docs/{AEP_SERVICE_INSTALL,DEPLOY_FRESH_MACHINE}.zh-CN.md`, `requirements/` | 多为 PowerShell 脚本，无 pytest 守线 | 无 claim、无 ACK | 不碰，等持有者认领 |
| B 额度接手 | `runtime/local_takeover_brain.py`, `src/federation/handoff_cli.py`, `tests/test_handoff_cli.py` | handoff_cli 9 passed | codebuddy 自报只做词表桥接、未碰此组 | 不碰，等持有者认领 |
| C 本地模型 | `scripts/run-local-model.ps1`, `tests/test_local_model_launcher.py` | 4 passed | 无 claim、无 ACK | 不碰 |
| D UIA/coordination tracked 修改 | `commander_uia.py`/`uia3.py`/`handoff_worker.py`/`relay_drill.py`/`presence.py`/`supervisor.py`/`core_host.py`/`foundation/io.py`/`relay/duty.py` 及对应 tests | 各定向测试全绿 | Codex 审计点名"队友 WIP"，无逐文件 ACK | 不碰，等逐块整合 |
| E 运行时数据 | `.pytest_*.log`, `uia_receipts/`, `hal-audit.txt`, `assets/` | 不适用 | 非源码产物 | 明确排除 |

## 已覆盖的 claim（不构成对 WIP 的授权）

- `qoder_cn / AEP-QUOTA-RELAY-20261122`：paths 仅 `docs/relay/*`、`src/relay/*`、`test_relay_*`，
  不含托盘或 local_takeover_brain。
- `copilot-github / AEP-CAPACITY-HEARTBEAT-20261122`：paths 泛化到 `federation/coordination/tests/docs`，
  但无逐文件 ACK，且已接近到期；不作为代提交依据。
- `workbuddy / AEP-PACKAGING-BACKUP-*`：paths 仅 `.tools`，已过期。

## 我（workbuddyai）本轮实际落地的

- 故障注入演练 `federation/capacity_drill.py` + `tests/test_capacity_drill.py` +
  `docs/CAPACITY_DRILL_EVIDENCE.zh-CN.md`：14 项 checks 全 PASS，隔离目录跑真实状态机，
  不碰任何上述 WIP，不伪造 ACK。
- 本台账 + 上面已发出的认领请求。

## 下一步（需要真实认领，不能由我代做）

1. 任一持有者回一条带 event id 的 AEP-ACK，写清"我负责 A/B/C/D 的哪些文件、已测试全绿、可以入库"。
2. 我按组在干净 worktree 验证依赖闭包与全量回归，显式路径逐组入库、发版。
3. 若始终无人认领：这些 WIP 保持未提交；不得为了"看起来跑通"而代提交或放宽测试。

## 2026-11-24：用户指示由 Codex 接手后续升级

- Codex 单独审查了组 D 中 `core_host.py`、`supervisor.py` 与对应两个测试的既有未提交改动：仅增加心跳中的进程创建时间字段及字段测试。现按用户接手指示补充双宿主令牌测试，并将这四个文件作为单独一组验证入库；不是声称这些原有改动由 Codex 编写。
- 同组其他 UIA、presence、handoff、foundation 等改动和 A/B/C/E 组继续保留，未因接手指示自动归属或批量提交。
- 只读实测旧 Core 作为 SYSTEM 运行，当前进程无法查询其创建时间，权限不足属于未知。此次源码提交不代表运行中的旧 Core 已更新，也不代表无权限的读取者能确认新版进程身份。
