# Windows 自动化适配器

可选 `windows` 依赖组用于增强 Agent 窗口发现、进程心跳和 UI Automation：

```powershell
pip install -e ".[windows]"
```

- `psutil`：读取 PID、进程启动时间、CPU/内存、退出和重启状态；
- `pywinauto`：按窗口标题、控件名称和 UIA 控件树定位，不依赖固定坐标。

这些库只提供执行能力，不负责会话身份判断。所有操作仍需通过 AEP 的 instance ID、workspace、事件 ID、租约和 readback/ACK 契约。没有依赖时，系统继续使用内置 Windows API、CDP、MCP 或文件信箱接力。
