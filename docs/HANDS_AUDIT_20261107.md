# 执行手脚审计：2026-11-07

范围：external_runner、external_tools、FACP 转换边界，以及 2026-11-10 补充审计的 session lease、commander、CDP 与监督器执行面；核对既有 agent-desktop 探测和 T3 本机服务证据。审计并非全部客户端生产验收。

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
|P0|同一 Python 进程的两个线程会把进程级重入误认为同一执行者，可能同时进入相同原会话|已修：lease registry 记录持有线程、获取中状态与深度；仅同一线程允许重入，其他线程必须等待或超时，不再把“同进程”当成“同一写入者”|
|P0|external runner 对显式 `coordination` 根路径未 canonicalize，且省略 `workspace_id` 时以 CWD 建 key；可与 UIA 创建不同 lease|已修：显式根先 canonicalize 到共同 `session_leases` 根；未给 workspace identity 时从 coordination 根派生，与 UIA 使用同一稳定命名空间；旧根仍作为 legacy evidence 读取候选|
|P0|`coord-cdp send`、commander 的 CDP 分支和无 profile 的 UIA fallback 可绕过 shared original-session lease|已修：新增 `cdp_session_delivery` 受控包装层，核对 websocket 的末段 target id、原会话 profile、stable marker 与 lease，再允许 DOM 发送；CLI 直发要求 `--agent`、精确 `--target-id` 和 profile；commander 缺 profile 时返回 `DEFERRED`，不退回 raw UIA/CDP 盲写|
|P1|CDP 的 preflight/busy/target 不匹配诊断可能覆盖既有 event 回执，抹掉上一路证据|已修：既有 receipt 一律保留；新的拒绝/冲突状态写到带请求摘要的 sidecar outcome receipt，且 commander 将 target mismatch 作为 `DEFERRED`，不会伪报为已输入|
|P0|transport 返回 `{ok:true}`、`sent` 或字符串 `DELIVERED` 即可能被误升级为目标已收到|已修：wake router 只在原会话消息可见（或等价 event/session 可见证据）时标记 `MESSAGE_VISIBLE`；只有匹配同事件、同会话的目标 ACK 才能升级 ACK；其余成功返回保持 `INPUT_SUBMITTED` 或 `UNCERTAIN`|
|P0|整窗 UIA/OCR 或侧栏标题可包含同一 session marker，仍可能向错误原会话输入|已修（v0.6.33 opt-in）：严格 profile 仅接受受限活动 header/model 区的唯一 marker、已登记 fingerprint、HWND + UIA runtime surface 与受限 composer 区；获得 composer 焦点后再次验证。UIA3 失败不得降级到无法绑定 proof 的 OCR/PowerShell 输入，button send 复用已证明 wrapper。|
|P1|调度器可把会话未知或只有输入证据的工作标为已完成，导致无人值守链路越过 ACK/结果核验|已修（可选严格策略）：supervisor 增加 `unattended-strict` completion policy；它要求同事件目标 ACK、正向结果回读、会话已核对且无 `session_unknown`。默认 `standard` 保持兼容，因此无人值守部署必须显式选择严格策略|
|P0|超时只管理直接子进程，孙进程可能继续且继承 pipe 可拖过 timeout|已修（Windows）：父持有 `AEPExternal-*` Job，bootstrap 先入 Job 再启动目标；超时终止 Job 并验 Job 消失。非 Windows 用进程组兜底，明确不写 Windows 级树终止证明|
|P1|stdout/stderr全量缓存在内存|已修：持续 drain + 每流可配置上限，回执记录见到字节数、截断和 capture 完整性|
|P1|`text` 非字符串或 `timeout=True/NaN/∞` 在预留后失败|已修：所有这些输入在预留前拒绝|
|P1|本地CLI会话与GUI协作会话没有自动身份映射|部分修：已可由同一人工/适配器验证过的逻辑 `workspace_id`、`session_provider`、目标、稳定 marker 和 canonical lease root 共用 lease；仍不能从窗口标题或进程名自动猜原生 CLI session|
|P1|PROCESS_EXITED不是产物验收|保持明确状态；严格监督策略已拒绝在无 target ACK/结果回读时完成，但具体业务的结果判读器和第三方 Agent 回读仍待接线|
|P1|FACP注册表、NATS与派工未接通|协议测试通过不能代表联邦执行成功|

## 验证

新增真实 Python 子进程 UTF-8 输入/输出及落盘结果、输出限额、跨进程同会话互斥、超时后会话不确定栅栏、非法输入预留前拒绝测试；Windows 真实 Job timeout → 目标 → 已知孙进程树终止测试。新增统一 lease 回归：不同 artifact root 仍必须进入同一 canonical lease root、已验证的 `t3code → uia` session alias 会阻断 UIA、CLI 正常退出但未证实消息可见仍会阻断、UIA 中断/损坏 receipt 必须 reconcile、旧版 sidecar 升级时不丢失、直投 `coord-uia` 不能绕过活跃原会话且 `NO_WINDOW` 等已证明未输入状态会安全释放。历史定向执行：`tests/test_session_lease.py tests/test_external_runner.py tests/test_external_tools.py tests/test_a11y.py tests/test_ui_fallback.py tests/test_commander_delivery_safety.py tests/test_uia_transcript_verdict.py` 为 **153 passed, 2 skipped, 9 subtests**（27.84s）。

2026-11-10 补充回归覆盖：同进程跨线程 lease 排他、external/UIA 根与 workspace identity 对齐、CDP session wrapper 的可见回读/繁忙/错误会话/发送后未知栅栏、`coord-cdp send` 的精确 target/profile 闸门、commander 的“无 profile 拒绝写入”与 CDP wrapper 委托、wake router 的反假成功语义、supervisor 的 `unattended-strict` 结果门槛。该批用例用于证明围栏逻辑；尚不构成第三方客户端消息已可见或 Agent 已 ACK 的实机证据。

2026-11-10 版本化复核：v0.6.30 元数据、生成的 skill manifest、模块入口版本冒烟与新增 CDP endpoint/receipt preservation 回归已纳入全量测试；`python -m pytest -q` 为 **1632 passed, 2 skipped**（205.64s，exit 0）。此数值仅证明本机源码回归，不改变下述真实目标会话生产门槛。

2026-11-11 v0.6.31 补充审计：Qoder/Qoder CN 的 versioned-updater executable 不再靠“同名
exe”隐式放行；仅在各自 profile 显式配置的绝对 `allowed_exe_roots` 内，才可把实际版本路径认作
受信任的同一客户端。session fingerprint 以配置的逻辑 exe 保持稳定，同时记录 `actual_exe` 与
`exe_match_mode`；越出根目录仍会失配。窗口 anchor 也改为 `CURRENT` / `STALE` /
`PID_TITLE_MISMATCH` / `UNCONFIGURED`，不会把 title 比较伪称成 fingerprint verdict。后台巡检切为
不抢前台的 observe 模式，无法可靠 OCR 时明确写 `BACKGROUND_CAPTURE_UNVERIFIED`。本版新增的
elevated worker 只处理一个已经 DEPOSITED 的精确 order，默认 preview、`-Apply` 才需交互式管理员
令牌；其 `EXECUTOR_STARTED` 回执不是送达、ACK 或结果。`session_attestation` 是短租约的前置证明
基础件；因现有整窗 OCR 尚不能严格证明“活跃原会话”，它**尚未**接入生产发送闸门。新增或修改的
回归随后由本版全量测试复核；这仍不是任何第三方原会话已经接单的实机结论。

2026-11-11 v0.6.32 纠正审计：v0.6.31 的 Windows CI 在干净 runner 上暴露了一处明确的诊断顺序
缺陷：显式传入外来 Python 时，注册器先检查可选的本地 capability runtime，导致错误报
`APPROVED_AEP_PYTHON_NOT_FOUND`，而非真实的 containment 拒绝
`PYTHON_NOT_APPROVED`。修复后，显式路径先与静态 repository-local allow-list 比较，只有默认
路径才查询已安装的 allow-listed runtime。本机按 GitHub Actions 相同入口执行
`python -m unittest discover -s tests -q` 为 **1305 tests, 4 skipped, exit 0**。这只证明补丁的
本机回归，v0.6.32 的远程矩阵仍需独立查看；v0.6.31 标签和失败记录保留为可追溯历史。

同日发布前只读实机枚举：Qoder（PID 43000）在
`D:\Programs\Qoder\.qoder-versions\0.5.7\Qoder.exe`，被识别为 `exe-trusted-root`；其
window anchor 为 `CURRENT`，已保存 fingerprint 与当前观测一致。Qoder CN（PID 40388）在自己的
`D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.5.7\Qoder CN.exe`，同样仅由其独立 root
认可；它的旧 anchor 是 `STALE`，且没有已保存 fingerprint，故保持未锚定。两次命令都是只读
`coord-client discover/fingerprint --show`，未聚焦窗口、未输入、未发送，也不构成 ACK 或业务结果。

2026-11-11 v0.6.33 严格会话审计：此前的 `session_attestation` 只是基础件，且整窗
capture/侧栏同名会话无法证明当前正文。现在 profile 可显式启用
`require_strict_session_attestation=true`：同一 session lease 与 desktop lock 中，UIA3
必须在独立的 `session_region`、`model_region`、`selector_region`、`composer_region` 完成
唯一性证明；surface 同时绑定可信实际 exe、PID、标题、HWND 和 UIA runtime id。无已登记
fingerprint、任何 marker 歧义、runtime 不可读、同 HWND 内会话切换、composer 不在受限区或
UIA3 写入失效，均在键入前保持 `SESSION_ATTESTATION_REQUIRED`。严格路线不会 OCR 降级。这个
修复已由模拟 UIA 树回归覆盖，但尚未对任一第三方客户端完成新鲜的消息可见、目标 ACK 和结果读回。

2026-11-09 本机复核：pytest 发现范围已显式限定为版本化的 `tests/` 与 in-package realtime 测试，因此 `coordination/acceptance_runs/` 产生的微型工作区不会再以同名 `test_*.py` 污染全量收集。执行 `python -m pytest -q`：**1587 passed, 15 skipped, 949 subtests passed**（216.08s，exit 0）。这证明现有源码与测试配置可回归；它不证明任何第三方 Agent 已被唤醒或接单。真实只读枚举事件 `EVT-DESKTOP-READ-20261107-002` 已通过新 Job bootstrap 列出本机 ChatGPT / Qoder CN / Qoder / WorkBuddy / WorkBuddyAI；该事件 `delivery_verified=false`，没有向任何第三方 Agent 发送任务。

## 尚未通过的生产门槛

- 本地会话锁不能替代全局 Workspace/浏览器资源 lease；目前 UIA、external runner 与受控 CDP session wrapper 已共用 canonical session lease，但 Browser Bridge 尚未形成对应的实际适配器与实机接线，仍需统一接入同一资源身份和上层 lease。
- 新 CLI/T3 session 与桌面原会话尚无自动、可核验的身份映射；只能共享已经显式验证且传入的 `workspace_id + session_provider + target + session marker + lease root`，不能从标题或进程名猜测。
- `PROCESS_EXITED` 只保留本地进程证据，且未验证时会保持会话 `UNCERTAIN`；wake router 与严格监督器已阻止其假升级，但仍未由真实第三方消息可见、同事件 ACK、产物判读器自动形成 `AGENT_ACK → TASK_RESULT_READBACK` 闭环。
- Job Object 保护 normally-created 后代；虚拟环境 redirector 的 silent-breakaway 风险会在回执中明示，不能被称为全对抗隔离。
- FACP 注册表、NATS 与真实派工尚未接通。
- `unattended-strict` 不是默认值；生产无人值守 worker 若未显式启用它，仍只能得到兼容性 `standard` 的完成判定，不能宣称已满足严格闭环。
- 尚未取得 Qoder、Qoder CN、WorkBuddy、WorkBuddy AI 或 CodeBuddy CN 在各自**原会话**中“消息可见 → 同事件 ACK → 结果回读”的新鲜实机证据；不能用 mail、进程、窗口、CLI 成功或单元测试替代。
- 严格会话模式需要每个客户端针对真实窗口/DPI/布局校准 session、model、selector、composer 四块区域，并确认 UIA runtime id 可读；未标定客户端必须保持 strict 关闭，不能以扩大区域或 OCR fallback 规避拒绝。

结论：执行入口由“实验级无树治理”提升为“**带跨路由原会话围栏、CDP 受控写入与严格完成策略的受限执行底座**”，但仍非端到端生产派工路径。UIA 已有原会话定位/输入/发送/可见读回/挑战 ACK；本轮将 thread-safe lease、external runner、profiled UIA、受控 CDP 路径接到同一 canonical root 与可证据的 session alias，并把常见的 transport 假成功降级为未证实状态。下一门槛是 Browser Bridge 实际适配、全局 Workspace 资源仲裁，以及在真实目标会话中自动把 `MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK` 接回调度器。Provider 数量、CLI 退出码、窗口存在、Job 存在或单元测试均不能替代该验收。
