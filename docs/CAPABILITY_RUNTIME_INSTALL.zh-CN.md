# AEP 能力运行时安装

AEP 的“手脚”采用独立虚拟环境，不覆盖已有 Python、`.tools/desktop-sdk` 或其他 Agent 环境。

## 已覆盖的基础能力

- `mcp==1.30.0`：MCP 工具底座；
- `playwright` + Chromium：浏览器/页面读回；
- `pywinauto`、`psutil`：Windows UIA、窗口和进程定位；
- `watchdog`：事件驱动文件/信箱监听；
- `opentelemetry`：执行链路观测；
- `duckdb`：本地结构化记忆与审计分析；
- 项目本身：AEP 调度、派工、回执和接力协议。

## 一键安装

在仓库根目录使用 PowerShell 7：

```powershell
pwsh.exe -File .\scripts\install-capability-runtime.ps1 -InstallBrowser
```

默认安装到 `.tools/agent-runtime`。如需向量检索扩展，再显式追加：

```powershell
pwsh.exe -File .\scripts\install-capability-runtime.ps1 -InstallBrowser -IncludeVectors
```

协议/执行增强保持可选，不会被基础安装隐式拉入：

```powershell
& $py -m pip install -e ".[a2a,model-routing]"   # A2A SDK + LiteLLM
& $py -m pip install -e ".[durable]"              # Temporal Python SDK
& $py -m pip install -e ".[desktop-agent]"         # pyautogui/uiautomation 兜底
```

这些 extras 只安装客户端库，不会自动启动 Temporal 服务、桌面 Agent 或视觉模型；安装完成仍需按 provider
验收门槛逐项探测和绑定原会话。

## Cua Driver（可选桌面 MCP Provider）

Cua Driver 要求独立的 Python 3.12–3.13 运行时；不要把它强行塞进 AEP 的核心环境。使用固定版本、隔离安装并完成 MCP 探测：

```powershell
pwsh.exe -File .\scripts\install-capability-runtime.ps1
pwsh.exe -File .\scripts\install-cua-runtime.ps1 -Install -Register -Probe
```

脚本不会执行上游远程安装脚本，也不会默认安装 `cua-perception`。Cua 的可选感知扩展包含 AGPL-3.0-only 的 OmniParser 及其他第三方组件，发布或远程托管前必须单独核对许可证和 NOTICE。详见 [Cua Driver 接入 AEP](CUA_DRIVER_INTEGRATION.zh-CN.md)。

脚本会拒绝覆盖已存在的环境；`-WhatIf` 只输出计划，不创建或下载文件。

## 冒烟验收

```powershell
$py = '.\.tools\agent-runtime\Scripts\python.exe'
& $py -m pip check
& $py -m agent_evolution_protocol coord-provider-catalog --status candidate
```

真正接入 Agent 前仍需完成 `L1 能力探测 → L2 会话绑定 → L3 真实派工/ACK`。安装成功不等于某个厂商 Agent 已登录、已定位到原会话或已完成派工。

## 第三方项目

Browser Use、Skyvern、LaVague、UI-TARS/UFO2、Open Interpreter、OpenHands、OpenAdapt、Appium 和 REA 保持候选 Provider 方式登记，按真实任务选择性安装。目录和验收规则见 [第三方执行生态接入策略](THIRD_PARTY_EXECUTION_ECOSYSTEM.zh-CN.md)。
