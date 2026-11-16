# AEP v0.6.43 收尾交接（2026-11-16）

## 当前提交目标

- 仓库：`https://github.com/love530love/agent-evolution-protocol.git`
- 分支：`main`
- AEP v0.6.43 代码提交：`a144c90656ca1b886084cef221b5fa30a2ef5ac7`（首次推送时的 `origin/main`）
- 本交接文档为基线之后的独立文档提交；复核当前 HEAD 以 `git log -1 --oneline` 为准
- 版本：`0.6.43`
- 对端 AEGS：`F:\PythonProjects1\AEGS`，`main @ 756c2e0925b2d836f8d53d9003f8e378adc64edb`（与 `origin/main` 相同，工作区干净）
- 交接人：`qoder_cn`
- 当前 claim：`AEP-ROBUSTNESS-20261028-QODERCN`，覆盖 `docs`、`src/agent_evolution_protocol`、`tests`，有效期至 `2026-11-17T09:18:40Z`

本交接以可复现证据为准。下列“已落地”不等于其他 Agent 已接单、已 ACK 或已部署。

## 无人值守闭环结论

- 运行：`20261115-2005`，状态 `FINISHED_QUEUE`，游标 `8/8`，`send_count=8`，无 preflight 挂起。
- 原始截止：`2026-11-15T20:01:57.149917Z`；恢复次数：`1`；实际截止：`2026-11-16T04:06:39Z`。
- 早间报告：`coordination/nightly_loop/report.md`；运行状态：`coordination/nightly_loop/state.json`。
- 计划任务 `AEP-Nightly-Codex`：`Next Run Time: N/A`，最后运行 `2026-11-16 10:06:47`（本地），结果 `0`，已按 `04:35` EndBoundary 收口。
- 8 轮结果全部为终态 `result`，AEGS 功能提交依次为 `3d27cfe`、`9584c69`、`a2e2e9f`、`b85b9ac`、`e1f88d0`、`60e79d8`、`c409b7a`、`e698188`；AEGS 最终测试 `32 passed / 0 skipped`。
- N08 事故：`COMPOSER_NOT_EMPTY` 正确拒绝覆盖 523 字草稿，但旧 Python 逻辑误判为 `NO_RECEIPT` 并冻结为 `uncertain`。只读核验草稿逐字一致、rollout 无 N08 用户消息、未发生输入后，复用草稿直接 Send；rollout 用户消息 `01a1e220-1bee-78e2-b921-2ceda3a065e8` 于 `2026-11-16T02:11:57.551Z` 确认送达，`02:16:44.479Z` 回读 `task_complete`。
- 证据边界：本闭环拿到 `MESSAGE_VISIBLE` + 终态 `TASK_RESULT_READBACK`，**没有独立 `AGENT_ACK` 证据**。`task_delivered=true`、窗口聚焦、信箱文件存在都不能当 ACK。

## v0.6.43 变更

| 面 | 已落地 | 位置 |
| --- | --- | --- |
| 重试前复核 | 任何重试或 HOLD 前先查同一 thread rollout 的 `UserMessage`；已提交则记 `DELIVER_RECOVERED`，不重发 | `nightly_loop.py` |
| 草稿安全 | 草稿与待发文本逐字一致才复用；不一致返回 `composer-not-empty` 并记 `HOLD_DRAFT_CONFLICT` 终态 | `uia_session.ps1`、`nightly_loop.py` |
| 预算 | 证明未发送的重试默认 3 次；preflight 连续 HOLD 默认 4 次；超限终态停止 | `nightly_loop.py` |
| 选择容差 | composer 底部容差为窗口高度 8%，中心必须仍在窗口内；保留 `input_rejected` 诊断 | `uia_session.ps1` |
| 报告 | 展示原始截止、恢复次数、`## 恢复记录`（含手工恢复）与新增状态字段 | `nightly_loop.py` |
| 错误分类 | stderr 已知拒绝 token 映射为可恢复/冲突状态，不再一律 `CLI_EXIT_n` | `nightly_loop.py` |

验证：

- `tests/test_nightly_loop.py`：`18 passed`（本版新增 6 个）。
- 夜间循环 + UIA 相关定向回归：`70 passed`。
- PowerShell 语法检查：`PARSE_OK tokens=17507`。
- AEP 全量回归（排除陈旧模板测试）：`1781 passed, 2 skipped`；唯一失败为 `tests/test_campaign_watcher_integration.py`，原因是外部 Codex 启动的 `campaign-watch.ps1`（当前 PID `20772`）持有全局互斥量 `Global\AEP-Campaign-Watch`，测试无法再起第二个 watcher。该失败与 v0.6.43 改动无关，未终止他人进程。
- AEGS 独立复核：`32 passed in 14.42s`；`takeover` PASS（`HO-1b9a3291c517`）；`doctor` 的 state/handoff 与外部锚点 PASS；journal `62/62` 且 head 相同；`evidence export` 指纹 `e698188`、`dirty_non_governance=false`。

## 复算命令

```powershell
$repo = 'K:\PythonProjects5\agent-evolution-protocol'
Set-Location $repo
$env:PYTHONPATH = "$repo\src"
$env:PYTHONUTF8 = '1'

git log -1 --oneline
git status --short

# 夜间循环定向回归
python -m pytest -q tests/test_nightly_loop.py

# 全量回归；若外部 watcher 仍在运行，预期仅 campaign watcher 集成测试失败
python -m pytest -q --ignore=tests/test_ui_profile_examples.py

# 只读：确认是哪个 watcher 持有全局互斥量（不要杀进程）
Get-CimInstance -ClassName Win32_Process -Filter "Name='pwsh.exe'" |
  Where-Object { $_.CommandLine -match 'campaign-watch' } |
  Select-Object ProcessId, CreationDate, CommandLine | Format-List

# 闭环状态与报告
aep --coord-root "$repo\coordination" coord-nightly-loop status
Get-Content "$repo\coordination\nightly_loop\report.md"

# AEGS 侧独立复算
Set-Location 'F:\PythonProjects1\AEGS'
$env:PYTHONPATH = 'src'
python -m pytest -q
python -m aegs.cli takeover .
python -m aegs.cli doctor .
python -m aegs.cli journal status .
python -m aegs.cli evidence export .
```

## 当前真实边界与禁止事项

- 不把本闭环宣称为 `AGENT_ACK` 完成；后续如需 ACK，必须由目标原会话回复同一事件 ID。
- 不终止外部 `campaign-watch`（当前 PID `20772`，父属 Codex 运行时）。它持有全局单例互斥量；如需跑该集成测试，先确认其所有者与用途，不能用全机 `taskkill` 或进程名模糊清理。
- 用户本地未提交改动保持不动：`commander_uia.py`、`ui_fallback.py`、`uia3.py`、`tests/test_ui_fallback.py`、`tests/test_uia3_driver.py`，以及未跟踪的 `hal-audit.txt`、`uia_receipts/`。不得 `git reset`、`git checkout --` 或 force-push。
- `tests/test_ui_profile_examples.py` 的 `qoder_cn` 模板陈旧失败在本版之前已存在，不属于 v0.6.43。
- 旧计划任务窗口已结束；继续夜跑必须使用新的 `run_id` 与时间窗口，不能复用已 `finished` 的 state 盲发。
- 不确定输入、`DISPATCHING`、`CEASED` 或已输入未回读的订单禁止自动重放；结果不明不得切换服务重试。

## 后续执行者首个工作顺序

1. 读 `aep --coord-root ... coord-digest --agent <your_agent>`、`aep inspect --markdown` 与本交接；核对 claim 是否过期，写前重新 claim。
2. `git status --short` 与 `git log -1` 确认工作树与当前 HEAD；用 `git merge-base --is-ancestor a144c90 HEAD` 确认 v0.6.43 已在历史中；不要覆盖用户未提交文件。
3. 跑 `tests/test_nightly_loop.py`；全量回归时按上面的 watcher 条件解读唯一失败。
4. 复核 AEGS `756c2e0` 与 N08 证据；如需继续协作，先与用户确认新的夜里窗口与任务队列，再以新 `run_id` 重建 `state.json`。
5. 若要提升验收级别，优先争取一次真实的 `AGENT_ACK`，再谈“队友已完成”。
6. watcher 集成测试与常驻 watcher 的互斥冲突可作为独立改进候选：测试使用独立互斥量或预检测已运行实例；但不得静默改变生产单例语义。

## 交接证据索引

| 证据 | 路径 / 值 |
| --- | --- |
| AEP v0.6.43 代码提交 | `a144c90656ca1b886084cef221b5fa30a2ef5ac7` |
| AEP 版本 | `0.6.43` |
| 夜跑状态 | `coordination/nightly_loop/state.json`（`FINISHED_QUEUE`） |
| 早间报告 | `coordination/nightly_loop/report.md` |
| N08 手工恢复 | `message_id=01a1e220-1bee-78e2-b921-2ceda3a065e8`，`submitted_at=2026-11-16T02:11:57.551Z` |
| N08 结果 | `e6981881e9891be88029ee20b34e573371cf3cd1`，`32 passed / 0 skipped，16.60s` |
| AEGS HEAD | `756c2e0925b2d836f8d53d9003f8e378adc64edb` |
| 夜跑说明 | `docs/coordination/NIGHTLY_LOOP_20261115.zh-CN.md` |

## 协作通知记录（本地信箱）

`2026-11-16T02:43Z` 用 `coord-send --kind context` 写入以下本地信箱。仅本地信箱落盘，未使用 `--dispatch`，未触碰任何客户端原会话；文件存在不等于已读或 ACK。

| 收件人 | 信箱消息 ID |
| --- | --- |
| `codex` | `20261116T024341880548Z-43d287f1` |
| `qoder` | `20261116T024349021182Z-96823fc2` |
| `codebuddy` | `20261116T024349404333Z-8879c9d4` |
| `codebuddy_cn` | `20261116T024349705064Z-007061e0` |
