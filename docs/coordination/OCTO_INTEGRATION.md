# OCTO 生态接入：实时会客厅，不替代作战账本

这里的 OCTO 专指 Mininglamp-OSS 的 [octo-web](https://github.com/Mininglamp-OSS/octo-web)、[octo-server](https://github.com/Mininglamp-OSS/octo-server)、[octo-ios](https://github.com/Mininglamp-OSS/octo-ios)、[octo-android](https://github.com/Mininglamp-OSS/octo-android) 和 [octo-cli](https://github.com/Mininglamp-OSS/octo-cli)，不是其他同名项目。

## 能力映射

| OCTO 能力 | AEP 用法 | 边界 |
| --- | --- | --- |
| Web/Electron 与 iOS/Android | 人和 Agent 的实时讨论、移动端感知 | 安装客户端不会自动接管本机其他 Agent 窗口 |
| 服务端消息与 WebSocket | 协作事件的可选实时扇出 | AEP 本地事件 ID、证据、租约仍是权威记录 |
| octo-cli JSON 消息操作 | Bot 发送事件通知，人工/Agent 查阅 | 投递和已读均不等于目标 Agent 接单 |
| OCTO Loop/Fleet | 未来可映射任务、专家团与执行记录 | 先验证权限、幂等和回执；不自动创建远程任务 |

本阶段只交付**显式、单次、默认预览**的 `octo-cli` 通知适配器。它不依赖 OCTO 安装，不启动服务，不获取令牌，不后台轮询，也不自动外发聊天全文。传输失败或超时属于 `UNKNOWN_DELIVERY`，必须先查远端消息历史，不能盲重试。原有本地信箱和 UI 唤醒路径保持可用。

## 无 Docker 的 WSL 原生试验路径

用户选择 WSL 原生部署，不使用 Docker。官方 [octo-server QUICKSTART](https://github.com/Mininglamp-OSS/octo-server/blob/main/QUICKSTART.md) 的非容器路线要求 Go ≥1.25、WuKongIM v2、MySQL 8（含 OCTO schema）和 Redis 7；文件模块还可能需要 S3 兼容存储。单独编译 `octo-server` 不能构成可发消息的端到端环境。Web 客户端另需 Node/pnpm 并指向该服务。

2026-10-13 **状态更新**：此处 `WSLC` 指 Windows 上的 `C:\Program Files\WSL\wslc.exe` 容器工具，不是 WSL 发行版。旧复测曾发现昆仑镜像只在 `wslc-cli-admin-love`，普通会话空表；当晚已用 `save/load` 把同一镜像 ID `9b5a960d9cc3` 复制到普通会话，并创建、启动同名 `azure_ellsworth` 容器。普通会话 `exec` 与 Torch 导入通过，但 `torch.xpu.is_available()` 为 False，完整编译门仍未验收。故“普通会话恒为空／必须由用户双击提权”仅是**迁移前历史结论**，不得当作现行规则。任何会话若见空表，应先核对会话和镜像，禁止盲目 `run/pull`；创建必须显式 `--pull never`。现行详情见 `REA_WSLC_BRIEFING_20261013.md` 与 FlagGems 协作区共享知识顶部。**该昆仑编译镜像不是 OCTO 服务依赖栈**；不得把它当作部署 OCTO 的 MySQL、Redis 或 WuKongIM 环境，更不能改写镜像。

先明确原生 OCTO 部署载体（如 `Ubuntu-Preview`，但不等同于 `wslc` 昆仑容器）并使 shell 可用；再只读检查 `go version`、`mysql --version`、`redis-server --version`、WuKongIM 是否存在及端口占用。之后选隔离目录、独立数据库和空闲端口，备份配置。只在这些条件满足时编译、配置和启动服务。不要复用现有 MySQL schema、Redis 数据库、昆仑编译容器或生产 Bot 令牌。

## 试用

在 OCTO 服务和 Bot 已由操作者配置、Bot 已有目标频道权限后，先确认本机 `octo-cli`、令牌环境变量与频道 ID。令牌不要写进仓库。上游命令支持 `OCTO_TOKEN` / `OCTO_BOT_TOKEN` 和自建服务的 `OCTO_API_BASE_URL`；具体权限取决于 Bot 类型与服务端策略。

```powershell
$env:PYTHONPATH = 'K:\PythonProjects5\agent-evolution-protocol\src'
python -m agent_evolution_protocol.coordination.octo_transport 'K:\path\to\existing-event.json' --channel-id 'chat-1'
```

默认仅打印将要发送的请求。确认频道、事件 ID 和内容后，显式增加 `--execute` 才发送一次；只有明确批准将任务正文带到 OCTO 服务器时再加 `--include-body`。请先用上游 `octo-cli schema message.send` 与 `octo-cli group list` 核对当前部署接口及频道。

```powershell
python -m agent_evolution_protocol.coordination.octo_transport 'K:\path\to\existing-event.json' --channel-id 'chat-1' --execute
```

返回的 `SENT_UNVERIFIED` 仅说明 CLI 收到成功响应；还需目标 Agent 用 AEP 原事件 ID 做明确 ACK，之后才能进入认领/执行状态。尚未在本机连接真实 OCTO 服务；目前只有离线契约测试。

## 后续验收门槛

1. 部署隔离的 OCTO 测试服务与测试 Bot；验证消息、权限拒绝、超时后查重、不同频道隔离。
2. 通过 WebSocket/受控事件入口接收回复，并用事件 ID 关联；禁止把普通聊天文本直接执行为指令。
3. 验证 OCTO read receipt、Agent ACK、任务认领三态不混淆，且离线队友归队后只补发未完成事件摘要。
4. 再考虑 OCTO Loop 与 AEP 作战任务双向映射；必须先解决冲突、租约与重复执行问题。
