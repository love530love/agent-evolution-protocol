# Orca 可选运行时适配器（开发预览）

归属体系仍是红皇后—鲶鱼—创造性破坏。Orca 负责终端承载与运行时交互，AEP 保留契约、证据、独立评审与晋级门；不会因为接入新宿主而更换治理原则。

## 实现范围

新增 `coord-orca configure|probe|bind|read|wait|send`，通过用户指定的本机 CLI 可执行文件调用 Orca。没有新增必须安装的 Python/Node 依赖，不复制上游 Electron 工程，不使用 Docker，不配置模型/API Key，不自动安装或启动 Orca。

本轮核对的上游合同版本：`ad4952e1dabcfff0871fa7a227cb00f0ac5d269b`。依据其 [CLI 文档源码](https://github.com/stablyai/orca/blob/ad4952e1dabcfff0871fa7a227cb00f0ac5d269b/docs/site/content/docs/cli/reference.mdx)、[JSON 信封](https://github.com/stablyai/orca/blob/ad4952e1dabcfff0871fa7a227cb00f0ac5d269b/src/shared/runtime-rpc-envelope.ts) 和终端契约开发；不表示其他版本自动兼容。上游主仓库为 MIT，后续若引入源码/制品仍需保留许可并核查第三方依赖。本模块为独立适配实现，未整包引入上游代码。

## 先只读验证

```powershell
# 必须是 Orca 自身 CLI 的绝对可执行文件路径，而不是桌面主程序或 .cmd/.ps1
aep --coord-root coordination coord-orca configure --command 'C:\实际位置\orca.exe'
aep --coord-root coordination coord-orca probe
# 从探测结果选择明确 handle，不使用 active/current
aep --coord-root coordination coord-orca bind --agent codex-orca --terminal '<实际handle>'
aep --coord-root coordination coord-orca read --agent codex-orca
aep --coord-root coordination coord-orca read --agent codex-orca --cursor '123'
aep --coord-root coordination coord-orca wait --agent codex-orca --timeout 15
```

所有调用显式使用 `--host local --json`，不用 shell 拼接。绑定运行时 ID、执行主机、PTY、进程 incarnation、工作区和观察到的 Agent 身份；缺失、不唯一、清单截断、主机范围未知或 PTY 非 live/writable 均拒绝。首版只允许观察到的 codex/claude 终端，不把普通 shell 当 Agent。Orca 重启或进程替换后旧绑定拒绝使用，当前无自动重绑定功能。配置和绑定已存在时拒绝覆盖，检查并保留旧证据后再显式处理。

`read` 只返回限量终端流和游标，不自动导入助手消息；`wait` 单次最多 30 秒，终端空闲不是接单、回复或完成。读取 stdout 可能包含敏感信息，应留本地，不默认上传远程。

## 显式发送

```powershell
# 默认预览，不读取终端、不输入
aep --coord-root coordination coord-orca send --agent codex-orca --event-id EVT-ORCA-001 --text-file notice.txt
# 明确授权后才真正输入；notice.txt 是发给 Agent 的短消息，不是 shell 脚本
aep --coord-root coordination coord-orca send --agent codex-orca --event-id EVT-ORCA-001 --text-file notice.txt --apply
```

发送要求上游声明 `terminal.prompt-delivery.v1`，重新核对绑定，并先持久记录 RESERVED；终端输入后记录上游 prompt receipt。全局本地适配器写锁避免本模块的并发发送。同 event-id 与内容重复调用只返回已有回执，不再次输入；内容或接收者改变则拒绝。消息限制 16000 UTF-8 字节并拒绝 ANSI/终端控制码，不更改模型、不新建会话/工作区、不 interrupt、不运行任意命令。

PROMPT_ACCEPTED 仅表示上游提供受支持的输入接收证据；超时、异常或不完整回执为 OUTCOME_UNKNOWN，CLI 返回 2，绝不自动重发。进程若在 RESERVED 后崩溃，后续重复调用仍不发送。输入内容会附事件 ACK 标记，但终端回显不算 ACK。

## 尚未证明的部分

- 本轮是固定契约的模拟回归，尚无本机真实 Orca 运行时验收。PATH 未发现可用 orca.exe 不代表系统所有目录都没有安装。
- 绑定的是终端进程，不是可认证的提供商会话或模型。conversation_session_verified=false；同一进程内切换会话仍可能无法识别。
- 未接 commander 自动投递或 transcript_bus 的助手归属证明，不能因该模块存在就称全军团已联通。
- 发送前检查与实际输入之间仍可能发生宿主变化，上游负责其 prompt-delivery 机制；本模块不是跨宿主事务、认证边界或安全沙箱。
- 有本地写权限者仍可改配置/回执；不应声称抵御恶意篡改。

下一验收：安装并核实 CLI → 只读 probe → 人工明确选择原终端 → 一条低风险消息 → 实际归属明确的回复 → AEP claim 与产物证据。完成前不得自动迁移现有 WorkBuddy/Qoder 原窗口。
