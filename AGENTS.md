# Agent Evolution Protocol 本地协作入口

- 接力开工前用 `coord-improvement policy` 查看模式；enforced 模式晋级必须持有效 token，旧 advance 也不能省略。模式切换需显式 actor/note，不能为图省事静默降级。该模式不代替身份认证或用户授权。

- 执行与自进化遵守 `docs/EVOLUTION_EXECUTION_PACT.zh-CN.md` v1.0。用 `coord-improvement propose|status|advance` 记录改进候选、验证、独立复核、试运行与回滚；采纳记录不代表实际部署或权限授予。

- 总体系为“红皇后—鲶鱼—创造性破坏”，先按 `docs/SYSTEM_OVERVIEW.zh-CN.md` 理解工作流与能力状态。复杂任务可用 `coord-reasoning create|status` 冻结任务契约；其中的授权与预算是声明，执行仍检查现有权限、租约与验收。

- 自治角色、干预及积分从 `docs/coordination/EXPERT_EVOLUTION_REVIEW_20261015.md` 接入：先发现角色契约，贡献须独立裁定后计入 AP/RP；身份声明不是认证，评审完成不直接授权上传。

- Windows 默认用 PowerShell 7 (`pwsh.exe`)；只有已证实的不兼容调用才隔离使用 Windows PowerShell 5.1。
- 首次在本机执行命令或遇到报错，先读 `docs/coordination/COMMAND_MEMORY.md` 并查询同类经验；有新坑请记录失败命令、退出码、真实错误和成功修复证据。
- Windows 编译/长路径先读 `docs/coordination/WINDOWS_BUILD_KNOWLEDGE.md`；长路径优先用受控 `subst` 短盘符映射，不覆盖现有盘符。
- 浏览器任务先读 `docs/coordination/BROWSER_CAPABILITY_ROUTING.md`：本 Agent 已验收内置浏览器优先，能力不足再使用 AI Browser Bridge。
- 不从聊天报告推断命令已验证；不把窗口聚焦、信箱投递当作对方 ACK；不确定的外部写操作先只读核查，禁止盲目重试。
- 涉及已授权发布制品、二进制或运行行为的结论时，按 `docs/coordination/REA_CAPABILITY.md` 选用 REA 并记录证据；普通完整源码问题先用源码工具。不得把静态分析说成已实测，也不得静默自动升级 REA。
- 用户已授权 Agent 在任务需要时主动启动管理员 CMD / PowerShell 7（包括 `Start-Process -Verb RunAs`），不必再为“是否可以发起提权”单独询问。先确认具体命令、目标与副作用，执行后核对进程权限、退出码和业务结果；不得把发起 UAC 当作已提权。此授权不能代替 Windows 的 UAC 批准，也不授权关闭 UAC、绕过安全边界或建立任意命令的无审计管理员后门。无人值守任务优先使用经一次性批准、限定操作和有日志的提升权限入口；入口不存在或 UAC 未批准时如实报告阻塞。
- WSLC 昆仑镜像已于 2026-10-13 复制到普通用户会话；旧记忆中的“必须管理员/普通会话恒为空”仅是迁移前状态。先读 `docs/coordination/REA_WSLC_BRIEFING_20261013.md` 的最新状态并核对 `images/inspect`；不得因空表盲目 `run/pull`，创建时使用 `--pull never`。物理 XPU 与完整编译门尚未验收。
- 紧急跨 Agent 指令按 `docs/coordination/URGENT_DELIVERY.md`：信箱投递、原会话消息可见、Agent ACK、记忆修改是不同证据级别。需即时唤醒时用 `coord-send --dispatch`；失败不得盲目重复发送。
- 本机桌面 MCP 备用能力按 `docs/coordination/DESKTOP_MCP_PROVIDERS.md` 登记与探测，统一入口 `coord-desktop inventory|probe|plan`；工具发现不等于原会话投递。结果不明的输入禁止切换服务重发。

<!-- AEP JOIN START -->
# Agent Evolution Protocol

If you are an AI agent opened in this workspace, first read `AEP_JOIN.md` and join the collaboration. Do not create a new session by default; use digest-first handoff, claim before writes, and guided retry after failures.
<!-- AEP JOIN END -->
