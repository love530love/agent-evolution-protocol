# Clawling ClawChat 集成基线

本项目支持 Clawling/ClawNest ClawChat 的机器通道（当前本机已验证
`app_version=1.0.30+208`，协议 `version=1`）。通道发现文件位于
`%APPDATA%\com.newbaselab\clawchat\machine_channel.json`，包含本地端口、短期令牌和
当前 roster。令牌只从发现文件读取，不写入仓库、日志或聊天记录。

## 已验证的原生 Agent

- `codex` / `native=codex`
- `claude` / `native=claudecode`
- `deepseek` / `native=deepseek_harness`

当前已实测完成一次 Codex 联系人绑定：`serve` 注册成功、`/v1/send` 返回
`ok=true,sent=1`，并成功发布一条 moment。连接码只在进程环境中短暂使用，未写入仓库。

## 运行边界

ClawChat GUI 是可选的外部协作通道，不是 AEP 的唯一唤醒后端。联系人注册成功
不代表后台模型回合一定可执行；必须另外看到真实 Agent 回复，才能标记为
`acknowledged`。若 ClawChat 返回 `401 Missing bearer` 或 `needs_login`，只记录为
`gui_connected_but_runtime_unverified`，不得阻塞 AEP 的本地信箱、UIA、浏览器桥接
和其他已验证调度路径，也不得反复消费连接码。

注册只证明机器会话可识别，不等于联系人已经添加。首次 `notify`/`send` 若返回
`NEEDS_CONFIRM`，必须由所有者在当前 ClawChat 会话明确确认；收到确认后再调用
`/v1/install/confirm`，并验证后续 `notify` 返回 `sent` 或 `ok`。

## 非原生 Agent 的接入原则

Qoder、WorkBuddy、QClaw 等不在当前 roster 中，不能冒充 native。它们应通过 AEP
桌面/UIA/浏览器桥接适配器接入，或以有明确 `serve` + `run` 的本地服务身份注册。
适配器必须报告四态：`discovered`、`registered`、`delivered`、`acknowledged`；
“输入框出现”或“调用返回”不能当作已接单。

## 使用

```powershell
pwsh -File .\scripts\clawling-register.ps1 -Agent codex -Name 'Codex AEP' -Native codex
```

脚本只做版本检查和会话注册，不自动确认联系人、不创建群组、不发送未经确认的消息。
建立协作群时使用 ClawChat 官方 `clawchat-orchestrate` 能力；AEP 的统一日志仍写入
本地协作区，作为跨 Agent 的可回溯事实源。
