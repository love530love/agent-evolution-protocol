# 外部执行与协作工具接入

提供隔离安装器、可执行发现/探测边界、既有会话消息计划与 MCP 配置。

安装：`pwsh.exe -File scripts/install-external-agent-tools.ps1`。
T3 npm 首次运行在本机遇到缓存目录 rename EPERM，可用 `scripts/install-t3-native.ps1` 下载固定版本、校验上游 SHA256SUMS 后直接解压，不修改用户的既有 T3 缓存。

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m agent_evolution_protocol.coordination.external_tools inventory
python -m agent_evolution_protocol.coordination.external_tools probe --provider agent_desktop
python -m agent_evolution_protocol.coordination.external_tools probe --provider t3code
```

- agent-desktop：通过 `status` 探测本机环境；MCP 配置见 `examples/external-tools.json`。安装后先读 `agent-desktop skills get desktop` 和 `skills get platform`。快照引用仅在对应快照有效。原会话身份、桌面租约与投递证据仍归 AEP 管理。
- T3 Code：通过 `t3 --help` 探测已安装入口；接入受支持 harness，外部 MCP 连接按上游 `docs/user/outside-agents.md` 配置。尚未实现会话任务适配，不自动安装后台服务。
- LobeHub：现已核实并安装官方 `@lobehub/cli@0.0.76`，提供 `lh hetero exec --type ... --resume ... --cwd ...` 的本地异构执行，及 `lh notify --topic ... --content ... --json` 的服务器唤醒。前者不要求服务器接入，后者需要 `lh login`。不得假定 CLI 原生会话与桌面 GUI 会话相同。

既有会话消息计划：`python -m agent_evolution_protocol.coordination.external_tools message-plan --provider t3code --session-id <ID> --text-file <UTF8文件>`；LobeHub 使用 `--provider lobehub` 和已有 topic ID。返回参数数组及 stdin，调用方须复用 AEP 订单幂等与资源租约；该命令本身不发送。

本机记录：agent-desktop 1.2.0 status 成功、list-apps 实际枚举 Qoder/Qoder CN/WorkBuddy 等进程；已登记 `coordination/desktop_providers/agent_desktop.json`，MCP tools/list 发现 67 工具（以回执实际列表为准）。LobeHub help/hetero/notify 契约实际读取成功，whoami 明确返回缺少认证。未做新的真实会话发送。

探测使用参数数组、固定超时、UTF-8 解码；探测成功不会被升级为投递成功。真实派工仍需原会话消息、目标回复及结果证据。

感谢 [Vercel Labs agent-desktop](https://github.com/vercel-labs/agent-desktop)、[T3 Code](https://github.com/pingdotgg/t3code)、[LobeHub](https://github.com/lobehub/lobehub) 的贡献。本次独立编写边界代码，仅引用上游，不复制或再分发其源码、模型或二进制。后续固定版本安装时核对对应 LICENSE/NOTICE。

用户于本轮明确选择本地自主可控：不连接 LobeHub 官方服务，不发起登录。`message_plan` 拒绝 LobeHub 服务器通知；`local_worker_plan` 仅生成 `lh hetero exec` 的本地原生会话恢复命令，提示经 stdin 输入，无服务器 topic/ingest。原生 CLI session 不等于桌面 GUI session，须显式绑定。

T3本机验收：独立原生运行时 `t3 v0.1.7` 通过；服务仅监听 `127.0.0.1:3783`，直连 HTTP 200；数据位于 `.tools/t3-data`。AEP 项目已注册，`thread list --base-dir <项目绝对路径>/.tools/t3-data --json` 返回 `[]`。没有创建模型会话或使用用户模型额度。现有会话发送计划会自动使用此隔离数据目录。服务当前为本地后台进程，尚未注册自启动。

已完成固定版本隔离安装入口、agent-desktop Provider 注册与67工具发现、T3既有会话消息计划、本地LobeHub异构执行计划。待完成真实任务投递、重复投递和跨重启验收；三者当前均未获得生产派工资格。
