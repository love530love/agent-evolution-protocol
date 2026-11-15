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
- 结果不明、投递不确定或 29 分钟内无结果：只 HOLD，不重发、不切换通道、不新开会话。

## 计划任务

- 名称：`AEP-Nightly-Codex`
- 窗口：`2026-11-15 20:30` → `2026-11-16 04:05`（本地时间），每 30 分钟一次，窗口结束自动停止。
- 启动器：`coordination/nightly_loop/tick.vbs` → `tick.cmd` → `aep --coord-root ... coord-nightly-loop run`。
- 任务以当前用户、最低权限、交互式登录令牌运行；不使用管理员权限，不修改 UAC。
- 日志：`coordination/nightly_loop/task.log`；状态：`coordination/nightly_loop/state.json`；报告：`coordination/nightly_loop/report.md`。

## 当前证据

- AEP：`v0.6.37` → `v0.6.38` → `v0.6.39` → `v0.6.40`，最新提交 `791aa17`，已普通推送 `origin/main`。
- AEP 全量回归：`1770 passed, 2 skipped`（排除已知陈旧的 `tests/test_ui_profile_examples.py`）。
- 第 1 轮事件 `AEP-NL-20261115-2005-01`：
  - 投递回执 `task_delivered=true`，事件见证控件为当前 `设计自进化架构` 文档；
  - rollout 回读 `task_complete`，结果行含 `AEP-RESULT:AEP-NL-20261115-2005-01`；
  - AEGS 功能提交 `3d27cfe`，治理证据提交 `b8dedae`；
  - AEGS 自报 `16 passed, 0 skipped, 11.00s`。
- 第 2 轮事件 `AEP-NL-20261115-2005-02` 已发送，等待回读。

## 早间检查

```powershell
aep --coord-root K:\PythonProjects5\agent-evolution-protocol\coordination coord-nightly-loop status
Get-Content K:\PythonProjects5\agent-evolution-protocol\coordination\nightly_loop\report.md
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

- 若 Windows 会话被锁屏或 Codex 被关闭，下一 tick 会在 preflight HOLD，不会盲发；这是设计行为，不是链路已通。
- 原始 UIA `session_marker` 可能在侧栏命中非活动会话，因此它只作辅助；主闸门是“全局最新 thread + 活动视图事件指纹 + rollout 回读”。
- AEGS 的 `PASS` 只是项目证据，不是 AEP 授权、claim/lease、ACK 或任务完成判定。
- `tests/test_ui_profile_examples.py` 的 `qoder_cn` 模板陈旧失败在本次工作前已存在，未混入本次改动。
