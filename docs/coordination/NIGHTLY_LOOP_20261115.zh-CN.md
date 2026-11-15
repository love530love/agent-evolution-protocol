# AEP × AEGS 无人值守协作闭环（2026-11-15 夜）

## 目标与边界

- 目标：在 8 小时窗口内，AEP 每 30 分钟推进一次与 Codex 原会话的协作；每轮要么发送一个新任务，要么回读上一轮结果，绝不并发写同一会话。
- 绑定原会话：`设计自进化架构`，Codex Desktop thread `01a09509-b3fb-7d92-9749-b1199c13938f`，工作目录 `F:\PythonProjects1\AEGS`。
- 活动视图指纹：上一轮事件 ID；每次发送前必须同时满足：
  1. 绑定 thread 仍是 Codex `state_5.sqlite` 中全局最近更新的 thread；
  2. UIA transcript 中出现当前活动视图指纹；
  3. composer 就绪、可驱动；
  4. 客户端路径与当前 Codex 版本一致。
- 结果以 Codex rollout 的 `task_complete.last_agent_message` 为独立回读源；UIA 的 `task_delivered=true` 只等于 `MESSAGE_VISIBLE`，不是 ACK。
- 结果不明、投递不确定或 60 分钟内无结果：只 HOLD，不重发、不切换通道、不新开会话。宽限刻意大于 30 分钟 tick 间隔，避免“结果在两次 tick 之间完成却被判超时”。
- 投递不确定时，额外用同一 thread 的 rollout 用户消息做独立确认；只接受 `UserMessage`，不接受助手回显或工具输出。

## 计划任务

- 名称：`AEP-Nightly-Codex`
- 窗口：`2026-11-15 20:30` → `2026-11-16 04:35`（本地时间），每 30 分钟一次，最后一次触发 `04:30`，用于在 `04:01` 运行截止后落最终报告；`04:35` 自动停止。
- 启动器：`coordination/nightly_loop/tick.vbs` → `tick.cmd` → `aep --coord-root ... coord-nightly-loop run`。
- 任务以当前用户、最低权限、交互式登录令牌运行；不使用管理员权限，不修改 UAC。
- 日志：`coordination/nightly_loop/task.log`；状态：`coordination/nightly_loop/state.json`；报告：`coordination/nightly_loop/report.md`。

## 当前证据

- AEP：`v0.6.37` → `v0.6.38` → `v0.6.39` → `v0.6.40` → `v0.6.41`（rollout 投递确认）→ `v0.6.42`（租约结算与 lease 阻塞恢复），代码与文档提交后普通推送 `origin/main`。
- AEP 全量回归：`1776 passed, 2 skipped`（排除已知陈旧的 `tests/test_ui_profile_examples.py`）。
- 第 1 轮事件 `AEP-NL-20261115-2005-01`：
  - 投递回执 `task_delivered=true`，事件见证控件为当前 `设计自进化架构` 文档；
  - rollout 回读 `task_complete`，结果行含 `AEP-RESULT:AEP-NL-20261115-2005-01`；
  - AEGS 功能提交 `3d27cfe`，治理证据提交 `b8dedae`；
  - AEGS 自报 `16 passed, 0 skipped, 11.00s`。
- 第 2 轮事件 `AEP-NL-20261115-2005-02` 已回读：AEGS 功能提交 `9584c69`、治理证据提交 `da11ca8`；自报 `18 passed / 0 skipped, 10.95s`。
- 第 3 轮事件 `AEP-NL-20261115-2005-03`：UI 回执为 `submitted-unconfirmed`，但同一 thread 的 rollout 用户消息在 `2026-11-15T12:13:12.091Z` 证明已送达（message id `01a1df20-3419-7af1-beff-a58879af5609`）。已按该独立证据恢复为 `sent`，没有重发。
- 第 4 轮事件 `AEP-NL-20261115-2005-04`：`2026-11-15T12:33:10.351Z` rollout 用户消息确认送达；`12:37:02.141Z` `task_complete` 回读；AEGS 功能提交 `b85b9ac`、治理证据提交 `55ca11a`；自报 `22 passed / 0 skipped, 14.00s`。本次结果回读在真实 tick 中触发 `RECONCILE_SESSION`，租约由 `SETTLED` 变为 `RECONCILED`，下一事件可重新获取租约。
- `v0.6.41` 把这条恢复逻辑固化：UI 回执不确定时，先在本 thread rollout 中确认用户消息；确认则记 `DELIVER_RECOVERED` 并继续等待结果，未确认才 HOLD。
- 第 4 轮首次投递被共享 session lease 拦下（`SESSION_UNCERTAIN`，未触达输入）：第 3 轮的 `submitted-unconfirmed` 在 `session_leases` 留下 `UNCERTAIN`。已用 rollout 双向证据（同一 event 的 `UserMessage` 与 `task_complete`）执行 `session_lease.reconcile`，租约变为 `RECONCILED`。
- `v0.6.42` 固化两条规则：结果回读或 `DELIVER_RECOVERED` 时自动用 rollout 证据 reconcile 该会话租约；若投递被 lease 拦住、收到 `SESSION_UNCERTAIN` 且 `previous_event_id` 已被 rollout 证明结算，则本事件证明未输入，记为 `DELIVER_NOT_SENT` 并重排，不重发上一事件。

## 早间检查

```powershell
aep --coord-root K:\PythonProjects5\agent-evolution-protocol\coordination coord-nightly-loop status
Get-Content K:\PythonProjects5\agent-evolution-protocol\coordination\nightly_loop\report.md
aep --coord-root K:\PythonProjects5\agent-evolution-protocol\coordination coord-codex-rollout submission --thread-id 01a09509-b3fb-7d92-9749-b1199c13938f --marker AEP-NL-20261115-2005-03
Get-Content K:\PythonProjects5\agent-evolution-protocol\coordination\nightly_loop\task.log -Tail 100
schtasks /Query /TN "AEP-Nightly-Codex" /V /FO LIST
```

独立复核 AEGS：

```powershell
git -C F:\PythonProjects1\AEGS log --oneline -10
git -C F:\PythonProjects1\AEGS status --short
python -m pytest -q   # 在 F:\PythonProjects1\AEGS
python -m aegs.cli takeover .
python -m aegs.cli journal status .
```

## 回滚

1. `schtasks /Delete /TN "AEP-Nightly-Codex" /F`
2. 将 `coordination/nightly_loop/state.json` 的 `stopped` 置为 `true`，或直接删除/停用计划任务。
3. `coordination/ui_profiles/chatgpt.json` 的原始备份为同目录 `chatgpt.json.bak-20261115-200129`。
4. 不执行 `git reset`、`git checkout --` 或 force-push；AEP 现有未提交 UI 文件与 `hal-audit.txt` 保持不动。

## 已知限制

- 若 Windows 会话被锁屏或 Codex 被关闭，下一 tick 会在 preflight HOLD，不会盲发；这是设计行为，不是链路已通。锁屏下 UIA 可能仍能读树，但模拟输入/OCR 可能失败，必须按 tick 回执判断。
- 单个任务超过 60 分钟仍无结果会终态 HOLD，不重发；8 小时窗口内剩余任务随之停止。
- `submitted-unconfirmed` 的 rollout 确认默认最多等 20 秒；若 rollout 不可读或滞后，仍按不确定 HOLD，不会改走其他通道重发。
- 原始 UIA `session_marker` 可能在侧栏命中非活动会话，因此它只作辅助；主闸门是“全局最新 thread + 活动视图事件指纹 + rollout 回读”。
- 活动视图若停留在历史位置，最新事件指纹不在渲染范围内，preflight 会 `ACTIVE_VIEW_FINGERPRINT_MISSING` 并 HOLD；把原会话滚到最新后再继续，不能改走其他通道。
- AEGS 的 `PASS` 只是项目证据，不是 AEP 授权、claim/lease、ACK 或任务完成判定。
- `tests/test_ui_profile_examples.py` 的 `qoder_cn` 模板陈旧失败在本次工作前已存在，未混入本次改动。
