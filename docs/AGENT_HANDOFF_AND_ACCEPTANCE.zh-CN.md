# Agent 接力、部署与真实验收

这份说明给后来加入的 Agent 使用。它把“代码已经改好”“本机监听器已部署”和“真实宿主已收到并完成任务”分成三种证据，任何一种都不能替代另外两种。

## 接入当前工作区

在项目根目录运行：

```powershell
python -m agent_evolution_protocol --coord-root coordination --kernel-root .aep-kernel workspace-init --workspace (Get-Location).Path --default-task <TASK-ID>
python -m agent_evolution_protocol --coord-root coordination --kernel-root .aep-kernel route --agent <AGENT-ID> --workspace (Get-Location).Path --task <TASK-ID> --intent '加入现有工作区并接力当前任务'
```

先阅读 `AEP_JOIN.md`、路由返回的 `next_action`、当前 digest 和任务状态。`next_action.argv` 是参数数组，只能先执行其只读动作；它不会创建会话、发送消息或认领任务。确认目标空闲后，再用 `coord-claim` 认领；完成一个阶段必须写 `coord-task-state` 和可复核证据。

若任务已被别人认领，读 owner、检查点和阶段回执，提交补位说明，不能直接抢占。原 Agent 释放或阶段变化后，监听器才会按稳定事件重新排队。

## 本机部署接力监听器

部署者先验证当前 Python、协作根和名单文件，再显式启动一个实例：

```powershell
pwsh.exe -NoProfile -File .\scripts\watch-improvements.ps1 -Python (Get-Command python.exe).Source -CoordRoot coordination -RosterFile <已核实名单.json> -ReadyFile .tools\watcher-ready.json
```

`READY` 只证明本次启动完成初次恢复；不是 Agent ACK 或持续健康证明。监听器只处理本地文件事件、不会调用模型、不会创建会话、不会直接输入桌面。静默时没有模型轮询。停止必须显式执行 `-Stop`，核对回执后人工移除 `.stop` 才能重启；锁冲突或 CLI 写失败会停机，不偷偷重放。

## 代码推送与真实验收

接力者在同一工作区先检查 `git status`，只提交属于本任务的源码、测试和公开文档；`.workbuddy-ai/`、令牌、聊天全文和本地协作状态不入库。运行 PowerShell 7 下的完整 `python -m pytest -q`、`python -m unittest discover -s tests -v` 和 `coord-improvement self-test`，保存输出路径与提交号。

推送后以 GitHub Actions 的所有矩阵作最终代码证据。CI 通过只说明仓库测试通过；还必须分别记录：监听器 READY、目标原会话唯一绑定、输入成功、消息读回、Agent ACK、任务认领、产物/外部结果。缺任一项就标为 `UNKNOWN` 或 `NOT_VERIFIED`，不能写“已完成”。

真实宿主验收应从无害的挑战 ACK 开始，保留原事件 ID，不新建会话，不在结果未知时重试。只有读回同一会话并得到可归属 ACK，才升级为 `MESSAGE_VISIBLE`/`AGENT_ACK`。上传、发布和删除还要有对账证据。

## 中断执行的接力

Windows 验证运行由命名 Job Object 管理子孙进程。`validation-runs --id <ID>` 出现 `INCOMPLETE` 或 `STOP_UNVERIFIED` 时，先只读预览：

```powershell
aep --coord-root coordination coord-improvement recover-run --id <ID> --run-id <RUN-ID>
```

只有确认原机、原会话的 Job 已不存在，并审查外部副作用后，才由有权限的接替者使用 `--apply --actor --note --evidence` 写入不可覆盖 `recovery.json`。恢复不会杀未知进程、不会释放旧租约、不会自动重跑或晋级；之后重新读取 handoff，再按普通 claim 流程继续。Linux 或没有停止证明的记录保持阻塞。

## 证据等级

`文件已写入` < `监听器已就绪` < `窗口已唯一定位` < `输入已提交` < `同会话可读` < `Agent ACK` < `认领` < `产物验收`。低等级证据不能向上冒充。任何 Agent 都可以提出任务、反证或代办请求，但实际执行者、授权者和验收者必须分别记录。
