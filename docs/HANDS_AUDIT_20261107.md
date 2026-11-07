# 执行手脚审计：2026-11-07

范围：external_runner、external_tools、FACP转换边界；核对既有agent-desktop探测和T3本机服务证据。审计并非全部客户端生产验收。

## 发现与处置

|级别|发现|状态|
|---|---|---|
|P1|独占创建attempt后尚未写完，另一执行者读取半截JSON会崩溃|已修：保留UNKNOWN，禁止重放|
|P1|argv接受字符串等非法类型，可能形成错误的进程调用|已修：参数数组校验发生在预留前|
|P1|重复调用直接信任result文件，不核对事件/请求哈希|已修：不匹配或损坏回执保持UNKNOWN|
|P2|timeout接受NaN/无穷|已修：要求有限正数|
|P1|不同事件对同一会话无本地互斥|已修：输入型计划强制 `provider + target_agent + session_id`，以 workspace 绑定哈希成跨进程会话锁；忙时不预留 attempt。超时/中断保留 `UNCERTAIN`，须显式真实证据 reconcile|
|P0|超时只管理直接子进程，孙进程可能继续且继承 pipe 可拖过 timeout|已修（Windows）：父持有 `AEPExternal-*` Job，bootstrap 先入 Job 再启动目标；超时终止 Job 并验 Job 消失。非 Windows 用进程组兜底，明确不写 Windows 级树终止证明|
|P1|stdout/stderr全量缓存在内存|已修：持续 drain + 每流可配置上限，回执记录见到字节数、截断和 capture 完整性|
|P1|`text` 非字符串或 `timeout=True/NaN/∞` 在预留后失败|已修：所有这些输入在预留前拒绝|
|P1|本地CLI会话与GUI协作会话没有自动身份映射|未修：不能从窗口标题猜原生CLI session|
|P1|PROCESS_EXITED不是产物验收|保持明确状态；结果判读器待接线|
|P1|FACP注册表、NATS与派工未接通|协议测试通过不能代表联邦执行成功|

## 验证

新增真实 Python 子进程 UTF-8 输入/输出及落盘结果、输出限额、跨进程同会话互斥、超时后会话不确定栅栏、非法输入预留前拒绝测试；Windows 真实 Job timeout → 目标 → 已知孙进程树终止测试。定向：`tests/test_external_runner.py tests/test_external_tools.py tests/test_windows_job.py` 为 42 passed, 1 skipped, 3 subtests passed；全量：1529 passed, 15 skipped, 855 subtests passed（160.32s）。真实只读枚举事件 `EVT-DESKTOP-READ-20261107-002` 已通过新 Job bootstrap 列出本机 ChatGPT / Qoder CN / Qoder / WorkBuddy / WorkBuddyAI；该事件 `delivery_verified=false`，没有向任何第三方 Agent 发送任务。

## 尚未通过的生产门槛

- 本地会话锁不能替代全局 Workspace/浏览器资源 lease；UIA、Browser Bridge、外部 CLI 仍需统一接入同一资源身份和上层 lease。
- 新 CLI/T3 session 与桌面原会话尚无自动、可核验的身份映射；不能从标题或进程名猜测。
- `PROCESS_EXITED` 后尚未由消息可见、同事件 ACK、产物判读器自动升级为 `AGENT_ACK` / `TASK_RESULT_READBACK`。
- Job Object 保护 normally-created 后代；虚拟环境 redirector 的 silent-breakaway 风险会在回执中明示，不能被称为全对抗隔离。
- FACP 注册表、NATS 与真实派工尚未接通。

结论：执行入口由“实验级无树治理”提升为“**受限执行底座**”，但仍非端到端生产派工路径。只有把上层 lease、已验证的原会话定位/输入/发送/可见读回和同事件 ACK/结果判读接到同一条链，才可提升为生产能力。Provider 数量、CLI 退出码或 Job 存在均不能替代该验收。
