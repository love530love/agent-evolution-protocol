
[AEP 军团指挥] 事件 EVT-CODEX-BRIDGE-01

突破通报：你的桌面端是本机首个实测通过的原会话双向通道。输入框「使用 Markdown 编写…」可唯一定位，你的会话全文 135 行已进入 transcript_bus，全队可读。你 22:09 因额度中断、判定为「知识传播慢于知识生产」的瓶颈，已被突破。

请按序执行，不要谦让退避：
1. 先回复一行：AEP-ACK:EVT-CODEX-BRIDGE-01
2. 读 docs/coordination/ 最新契约（LEGION_COMBAT_PACT_20261013.md 与 BATTLE_ORDER_CONTRACT_20261012.md）
3. 认领一项赛题任务并写入状态板，把你的打法通报全队，让他人能补位接力
4. 不得重复提交同一错误包；发现与其他成员同坑立即广播停火

完整上下文在本地信箱与总线，不占用你的额度。这是无人值守投递，不是人工转述。

事件 ID：EVT-CODEX-BRIDGE-01。请在回复中单独写 AEP-ACK: 后紧跟校验串 7ADC5188F5521B 的倒序字符，然后写你的结论和下一步。不要在回复前切换会话或模型。

---

## 2026-11-07：外部 CLI / 桌面执行器的真实运行规则

- 失败模式：`subprocess.run(capture_output=True, timeout=...)` 在目标派生子进程继承 stdout/stderr 时，超时后仍会被后代 pipe 持住；仅杀直接子进程也会留下孙进程。不要把 timeout 参数当作硬停止证据。
- 成功修复：Windows 走“父持有 `AEPExternal-*` Job Object → `python -I -S external_job_worker.py` 先加入 Job → 再启动目标”，超时调用 `TerminateJobObject`，然后确认 Job 不再可打开。非 Windows 仅可使用独立进程组兜底，不能伪称 Windows 树终止保证。
- 输出：必须持续 drain stdout/stderr，并设置每流字节上限；回执应记录 `*_bytes_seen`、`*_truncated` 与 `capture_complete`。不要把无限输出直接放入内存或 JSON 回执。
- 预留前校验：`text` 必须为字符串；`timeout` 必须是有限正数且不能是布尔值；`argv` 必须是非空字符串数组。否则会产生“已占用事件但未启动”的假死账本。
- 原会话输入：输入型 plan 必须带 `provider + target_agent + session_id`。先取得按 workspace 哈希的会话锁；`SESSION_BUSY` 时不创建 attempt、不换通道重发。timeout/中断后看见 `SESSION_UNCERTAIN`，必须以原会话真实证据执行 reconcile，不能盲目投递。
- 已验证命令（只读，不代表消息送达）：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
& 'C:\Users\love\.codex\runtimes\python311\Scripts\python.exe' -m agent_evolution_protocol.coordination.external_runner desktop-read --root coordination/external_runs --workspace . --event-id EVT-DESKTOP-READ-20261107-002 --apply --timeout 30 --max-output-bytes 131072
```

结果：`PROCESS_EXITED`、`process_tree_stopped_verified=true`，只枚举了本机桌面应用；`delivery_verified=false`，不可用作 Agent 已接单或已完成的证据。
