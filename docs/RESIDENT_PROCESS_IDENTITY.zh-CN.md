# 常驻状态：心跳与进程身份

`resident_status` 分别报告 `heartbeat_fresh`、`pid_alive`、`pid_identity`；只有新鲜心跳、OS 进程存在且创建时间与心跳的 `process_started_at` 相符时才输出 `HEALTHY`。PID 死亡显示 `DEAD_PROCESS`，创建时间不符显示 `PID_MISMATCH`，权限不足、未知 PID、无法取得创建时间或缺少字段显示 `PROCESS_UNKNOWN`，均不会产生 `READY`。

已有旧心跳没有 `process_started_at` 时，即使时间新鲜、PID 存在也不能证明它不是 PID 复用，故显示 `PROCESS_UNKNOWN`。时间令牌必须带时区且至少毫秒精度；粗粒度令牌视为未知，避免沿用旧的 2 秒容差误判快速 PID 复用。对应宿主需在进程启动时记录 `process_identity.current_started_at()` 到每次心跳的 `process_started_at`，部署新版并重启该宿主后才可能核验成功；为空值仍视为未知。对现有托盘、运行任务或其他 Agent 本变更不进行注册、重启或输入。

不同权限可能导致 Windows 进程查询被拒；拒绝是未知而不是进程已经死亡。控制平面 `READY` 仅表示必需进程及循环有验证过的心跳，不表示已定位特定会话、完成发送或收到 ACK。
