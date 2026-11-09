# 执行手脚审计：2026-11-07

范围：external_runner、external_tools、FACP转换边界；核对既有agent-desktop探测和T3本机服务证据。审计并非全部客户端生产验收。

## 发现与处置

|级别|发现|状态|
|---|---|---|
|P1|独占创建attempt后尚未写完，另一执行者读取半截JSON会崩溃|已修：保留UNKNOWN，禁止重放|
|P1|argv接受字符串等非法类型，可能形成错误的进程调用|已修：参数数组校验发生在预留前|
|P1|重复调用直接信任result文件，不核对事件/请求哈希|已修：不匹配或损坏回执保持UNKNOWN|
|P2|timeout接受NaN/无穷|已修：要求有限正数|
|P0|外部 runner 的 artifact root 与 UIA coordination root 不同，相同 key 仍落到不同锁/sidecar，可跨路由双发|已修：所有合规写路由统一使用 `<coord-root>/session_leases/`；external runner 以 `--session-lease-root` 显式加入，旧 artifact root 的 unresolved sidecar 仍会被读取/阻断，直到 evidence reconcile|
|P0|输入型 CLI `PROCESS_EXITED` 被误当会话终态，`delivery_verified=false` 时仍会放行下一写入|已修：没有同原会话消息可见证据时，一律将 lease 留为 `UNCERTAIN`；进程退出仅保留为 runner result，不能解除原会话围栏|
|P1|不同事件对同一会话无本地互斥|已修：外部执行器、profiled UIA relay、`coord-uia deliver/compose` 复用 `session_lease`；键为稳定 `workspace_id + session_provider + target_agent + session_marker/session_id`，不用 PID。transport provider 与 session identity 分离；跨 transport 别名必须有 `session_binding_evidence`。忙时不预留 attempt；超时/中断保留 `UNCERTAIN`，须显式真实证据 reconcile|
|P0|超时只管理直接子进程，孙进程可能继续且继承 pipe 可拖过 timeout|已修（Windows）：父持有 `AEPExternal-*` Job，bootstrap 先入 Job 再启动目标；超时终止 Job 并验 Job 消失。非 Windows 用进程组兜底，明确不写 Windows 级树终止证明|
|P1|stdout/stderr全量缓存在内存|已修：持续 drain + 每流可配置上限，回执记录见到字节数、截断和 capture 完整性|
|P1|`text` 非字符串或 `timeout=True/NaN/∞` 在预留后失败|已修：所有这些输入在预留前拒绝|
|P1|本地CLI会话与GUI协作会话没有自动身份映射|部分修：已可由同一人工/适配器验证过的逻辑 `workspace_id`、`session_provider`、目标、稳定 marker 和 canonical lease root 共用 lease；仍不能从窗口标题或进程名自动猜原生 CLI session|
|P1|PROCESS_EXITED不是产物验收|保持明确状态；结果判读器待接线|
|P1|FACP注册表、NATS与派工未接通|协议测试通过不能代表联邦执行成功|

## 验证

新增真实 Python 子进程 UTF-8 输入/输出及落盘结果、输出限额、跨进程同会话互斥、超时后会话不确定栅栏、非法输入预留前拒绝测试；Windows 真实 Job timeout → 目标 → 已知孙进程树终止测试。新增统一 lease 回归：不同 artifact root 仍必须进入同一 canonical lease root、已验证的 `t3code → uia` session alias 会阻断 UIA、CLI 正常退出但未证实消息可见仍会阻断、UIA 中断/损坏 receipt 必须 reconcile、旧版 sidecar 升级时不丢失、直投 `coord-uia` 不能绕过活跃原会话且 `NO_WINDOW` 等已证明未输入状态会安全释放。定向执行：`tests/test_session_lease.py tests/test_external_runner.py tests/test_external_tools.py tests/test_a11y.py tests/test_ui_fallback.py tests/test_commander_delivery_safety.py tests/test_uia_transcript_verdict.py` 为 **153 passed, 2 skipped, 9 subtests**（27.84s）。

2026-11-09 本机复核：pytest 发现范围已显式限定为版本化的 `tests/` 与 in-package realtime 测试，因此 `coordination/acceptance_runs/` 产生的微型工作区不会再以同名 `test_*.py` 污染全量收集。执行 `python -m pytest -q`：**1587 passed, 15 skipped, 949 subtests passed**（216.08s，exit 0）。这证明现有源码与测试配置可回归；它不证明任何第三方 Agent 已被唤醒或接单。真实只读枚举事件 `EVT-DESKTOP-READ-20261107-002` 已通过新 Job bootstrap 列出本机 ChatGPT / Qoder CN / Qoder / WorkBuddy / WorkBuddyAI；该事件 `delivery_verified=false`，没有向任何第三方 Agent 发送任务。

## 尚未通过的生产门槛

- 本地会话锁不能替代全局 Workspace/浏览器资源 lease；目前 UIA 与 external runner 已共用 canonical session lease，但 Browser Bridge、CDP 等写路由仍需统一接入同一资源身份和上层 lease。
- 新 CLI/T3 session 与桌面原会话尚无自动、可核验的身份映射；只能共享已经显式验证且传入的 `workspace_id + session_provider + target + session marker + lease root`，不能从标题或进程名猜测。
- `PROCESS_EXITED` 只保留本地进程证据，且未验证时会保持会话 `UNCERTAIN`；尚未由消息可见、同事件 ACK、产物判读器自动升级为 `AGENT_ACK` / `TASK_RESULT_READBACK`。
- Job Object 保护 normally-created 后代；虚拟环境 redirector 的 silent-breakaway 风险会在回执中明示，不能被称为全对抗隔离。
- FACP 注册表、NATS 与真实派工尚未接通。

结论：执行入口由“实验级无树治理”提升为“**带跨路由原会话围栏的受限执行底座**”，但仍非端到端生产派工路径。UIA 已有原会话定位/输入/发送/可见读回/挑战 ACK；本轮把它与外部执行路径的会话侧车接到同一 canonical root 与可证据的 session alias。下一门槛是 Browser Bridge/CDP 加入同一 lease、全局 Workspace 资源仲裁，以及把 `MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK` 的可证据升级自动接回调度器。Provider 数量、CLI 退出码或 Job 存在均不能替代该验收。
