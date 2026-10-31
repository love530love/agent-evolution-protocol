# Desktop wake locator contract

桌面唤醒不是“找到一个窗口然后按坐标”。每次投递必须按以下顺序完成，并把
每一步写入同一个 event receipt：

1. **进程锚定**：`process_name` + `expected_path`，必要时再加 `expected_pid`。
   路径无法验证时拒绝，除非人工明确启用 `AllowUnknownPath`。
2. **窗口唯一性**：只允许一个可见主窗口；0 个窗口只能在显式
   `AllowLaunch` 与 `AEP_UIA_ALLOW_LAUNCH=1` 同时存在时启动。多个窗口直接停止，
   不能猜测或循环点击。
3. **会话唯一性**：优先 UI Automation 的精确 `Name` + `SelectionItem/Invoke`；
   OCR 只能作为视觉回退，并且要求唯一命中、窗口完全可见、命中位于侧栏。
4. **编辑器/发送控件**：重新读取 UIA 树后定位输入控件和发送按钮；不复用旧
   ref、旧坐标或旧窗口句柄。若 Electron 客户端没有可访问性树，只能使用配置中
   经过现场截图校准的窗口比例（`send_button_x_ratio/y_ratio`），并在点击后重新
   OCR：输入框必须清空，且 event id 不能仍位于编辑器下方区域。输入内容必须包含
   event id，并在发送前验证已进入编辑器。
5. **闭环验收**：发送后读取同一窗口/同一会话，要求 event id 出现在消息区且编辑器
   已清空。只有同时满足才是 `task_delivered=true`；输入、点击、截图、HTTP 200
   都不是 ACK。

Windows 前台切换也有独立证据：执行器会把当前线程临时附加到目标线程和当前前台
线程，调用 `BringWindowToTop/SetForegroundWindow`，再核对 `GetForegroundWindow`
是否等于目标 HWND。若系统仍拒绝前台权限，任务保持未投递，不会把“窗口已找到”当成
唤醒成功。

## 推荐调用

```powershell
pwsh -File src/agent_evolution_protocol/coordination/wake_adapters/uia_session.ps1 `
  -Agent qoder_cn -ProcessName 'Qoder CN' -ExpectedPath 'D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.4.11\Qoder CN.exe' `
  -ExpectedTitle 'FlagGems-sglang' -Mode deliver -SessionLabel 'FlagGems-sglang' `
  -EventId 'WAKE-...' -Text '... WAKE-...' -OcrVerify
```

当窗口不唯一、会话标签不唯一、输入框没有可访问性控件、发送按钮无法通过 UIA/OCR
或经现场校准的比例定位时，适配器必须返回结构化失败，而不是降级到右下角盲点。
失败事件留在
`wake_failed/`，由调度器引导重试或切换到另一条适配器通道。

## 多 Agent 会话锚点

### Qoder 双客户端身份（必须分开）

国际版 Qoder 与国内版 Qoder CN 是两个不同的桌面客户端，不能用标题
`Qoder` 的子串匹配来代替身份判断。注册和唤醒时同时使用精确窗口标题、进程名和
可执行文件路径：

| agent | 窗口标题（精确） | 进程/可执行文件 |
| --- | --- | --- |
| `qoder` | `Qoder`（黑色任务栏图标，国际版） | `Qoder.exe` / `D:\Programs\Qoder\.qoder-versions\0.4.11\Qoder.exe` |
| `qoder_cn` | `Qoder CN` | `Qoder CN.exe` / `D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.4.11\Qoder CN.exe` |

`qoder` 的匹配不能命中 `Qoder CN`，`qoder_cn` 的匹配也不能命中 `Qoder`。
若进程路径或标题无法同时核实，保持 `UNRESOLVED`，不得发送。

每个 Agent 的 `coordination/clients/anchors/<agent>.json` 至少记录：

```json
{
  "agent": "qoder_cn",
  "provider": "uia",
  "process_name": "Qoder CN",
  "expected_path": "D:\\Programs\\QoderCN\\Qoder CN\\.qoder-versions\\0.4.11\\Qoder CN.exe",
  "expected_title": "FlagGems-sglang",
  "session_label": "FlagGems-sglang",
  "session_id": "optional-provider-session-id",
  "workspace": "K:\\PythonProjects5\\FlagGems-sglang",
  "updated_at": "..."
}
```

`session_id` 是提供方原生 ID；缺失时仍必须使用窗口+会话标签锚点，不能因为缺失
就新建会话。调度器每次动作前重读锚点并输出 `anchor_digest`，便于发现窗口切换、
重启或同名会话漂移。

UIA transport 只在发现结果唯一且执行器提供新 accessibility tree 时执行。服务超时、
窗口候选不唯一或会话无法重新定位时，必须保留任务并返回结构化失败；禁止坐标盲点、
自动新建会话或伪造 delivered/ACK。
