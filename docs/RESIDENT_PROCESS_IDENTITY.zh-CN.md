# 常驻状态：心跳与进程身份

`resident_status` 分别报告 `heartbeat_fresh`、`pid_alive`、`pid_identity`；只有新鲜心跳、OS 进程存在且创建时间与心跳的 `process_started_at` 相符时才输出 `HEALTHY`。PID 死亡显示 `DEAD_PROCESS`，创建时间不符显示 `PID_MISMATCH`，权限不足、未知 PID、无法取得创建时间或缺少字段显示 `PROCESS_UNKNOWN`，均不会产生 `READY`。

已有旧心跳没有 `process_started_at` 时，即使时间新鲜、PID 存在也不能证明它不是 PID 复用，故显示 `PROCESS_UNKNOWN`。时间令牌必须带时区且至少毫秒精度；粗粒度令牌视为未知，避免沿用旧的 2 秒容差误判快速 PID 复用。对应宿主需在进程启动时记录 `process_identity.current_started_at()` 到每次心跳的 `process_started_at`，部署新版并重启该宿主后才可能核验成功；为空值仍视为未知。对现有托盘、运行任务或其他 Agent 本变更不进行注册、重启或输入。

不同权限可能导致 Windows 进程查询被拒；拒绝是未知而不是进程已经死亡。控制平面 `READY` 仅表示必需进程及循环有验证过的心跳，不表示已定位特定会话、完成发送或收到 ACK。

Core 和 Desktop 宿主的源码现在会在心跳中写入进程创建时间令牌。已运行的旧进程必须在适当的维护窗口重启才会加载新代码；即使新进程有令牌，状态读取者没有权限查询其 PID 时仍会显示 `PROCESS_UNKNOWN`。不要以添加字段或启动进程代替实机核验。

Desktop 注册器支持默认无执行时长上限、失败有限重启和隐藏窗口；使用 `-ObserveOnly` 才能注册为仅回读模式。重启次数耗尽、正常退出或用户未登录时并不保证自动恢复；测试注册、实际计划任务注册、运行中进程和跨重启状态应分别核验。2026-11-27 本机用户态试建任务返回 `Access is denied`，之后经管理员路径成功更新 `AEP-DesktopHost`，读回 XML 确认系统 PowerShell、隐藏窗口、观察模式、`PT0S` 和最多三次失败重启；计划任务已启动并核验两个心跳周期。跨登录及真实故障后重启尚未实测。
