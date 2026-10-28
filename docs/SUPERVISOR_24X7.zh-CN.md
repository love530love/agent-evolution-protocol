# 7×24 Supervisor

启动监督器（前台可观察）：

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\aep-supervisor.ps1
```

注册为当前用户登录时启动的任务（显式执行才会修改系统）：

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\register-supervisor-task.ps1
```

Supervisor 只处理已有任务和事件，不自动创建会话，不对空闲 Agent 轮询，不绕过平台授权。默认每 30 秒执行一次有界 tick；需要更低开销时安装 `.[events]`，由文件事件唤醒补充监督。
