# 自然语言协作路由

工作区初始化后，新 agent 可以接收一句话：“请加入这个工作区协作，接入 T125。”
读取工作区的 AEP_JOIN.md 或 AGENTS.md，再运行：

```powershell
aep route --agent codex --intent "请加入这个工作区协作，接入 T125" --markdown
```

route 读取当前协作状态并提供指引，本身不会认领任务、执行上传或启动其他模型。
按输出读取摘要、加入任务，然后在任务无人持有时执行 claim 指引。
输出中的 `<RESOURCE>`、`<OPERATION_KEY>`、`<reason>` 等是待填模板，需要根据真实任务补齐后执行。

| 场景 | 自然语言示例 | 下一步 |
| --- | --- | --- |
| project-orientation | 加入这个项目 | 读取态势和候选任务 |
| task-join | 接入 T125 | 摘要、加入、检查认领 |
| takeover | 接手 WorkBuddy 的 T125 | 检查持有人、检查点和交接依据 |
| review-only | 只审查上传方案，不执行 | 输出审查意见，禁止认领实现任务和外部写入 |
| isolated-exploration | 各自独立探索方案 | 独立形成候选，提交后再揭示 |
| shared-resource-lock | 上传提交 T125 | 先取得资源租约并登记操作 |
| stalled-recovery | WorkBuddy 卡住了 | 检查状态和操作结果，再恢复 |
| wake-agent | 通知 qoder 看 T125 | 显式唤醒请求，优先已有会话 |
| status-report | 汇报当前情况 | 只读汇报 |
| cli-less-fallback | 不支持 CLI，只能读文件 | 读取接入文档，由命令型 agent 协助 |

显式“不要执行”优先于上传或接手词语。普通消息不会自动启动模型；真实唤醒取决于接收方适配器。
UNKNOWN 外部操作必须先核实结果，避免重复提交。静默状态不需要模型轮询。
当前分类使用规则词表；复杂或含糊意图需要 agent 结合输出和用户原话判断，不能把分类当作授权。

## 项目外调用和任意任务

Agent 没有把项目设为当前目录时，用 `--workspace` 指向共享文件夹。它读取该工作区的 `.aep/workspace.json`，定位协作数据和恢复内核：

```powershell
aep route --agent hermes --workspace 'K:\PythonProjects5\MyProject' --task DOCS-INTRO --intent '接入文档改进任务' --markdown
```

任务 ID 不限赛题编号：`T125`、`BUG-42`、`DOCS-INTRO` 都可用。ID 为 1–64 个 ASCII 字母、数字、下划线或连字符，首字符为字母或数字，规范为大写，不能使用 Windows 设备名。一般描述继续写在 `--intent`；不易从自然语言识别的 ID 用 `--task` 明确传入，也可在初始化时用 `--default-task` 配置。

多个 agent 协作同一任务时共享同一协调根目录，但各自仍有独立身份和会话。辅助文件夹不会自动等于当前工作目录；指定 `--workspace` 可消除歧义。任务认领是执行前检查，不会因为读了路由建议就自动发生。

## 看懂路由解释

JSON 使用 `agent-evolution-route-v1`。除 `scenario`、任务、会话及 `first_commands`，还包含 `matched_rules` 命中规则和 `decision_trace`；可从中检查哪个词触发了场景，以及是否存在否定或冲突。

`confidence` 表示规则匹配强度，不是统计准确率。`requires_clarification` 表示当前意图需要进一步明确。分类无法覆盖所有自然语言句式；先检查与用户原话是否一致，再执行适用的建议。显式不执行和只读约束不受自定义词表覆盖。

恢复指引结合 owner 状态、时间和 checkpoint 判断下一步。等待用户或外部事件不等于失败；状态过期只提示复核，不会自动解除他人认领。UNKNOWN 或执行中操作要先核实结果，避免把重试变成重复提交。资源类别建议会选取对应的租约类型；它不代表租约已经取得。

## 项目词表

可以在共享工作区新建 `.aep/routing.json`：

```json
{
  "schema": "agent-evolution-routing-v1",
  "keywords": {
    "task-join": ["来搭把手"],
    "review-only": ["挑挑毛病"]
  },
  "replace_keywords": {
    "isolated-exploration": ["独立实验", "各自想方案"]
  }
}
```

`keywords` 补充默认词表；`replace_keywords` 替换该场景的普通词表。每个场景最多 64 个短语，每个短语最多 120 字符，配置文件最多 64 KiB。关键词是普通文本，不执行代码或正则。场景名只接受上表十种；未知字段、场景或无效类型会报错。内置只读、能力限制、状态查询及外部写保护规则不能通过配置移除。用 JSON 输出检查 `matched_rules`、`decision_trace` 和 `scenario` 后再使用。

`recovery.status` 区分 active、waiting、suspected-stale、terminal、unknown 和缺失持有人信息；`automatic_takeover_allowed` 始终为 false。`hazards` 给出有效及过期租约数、相关 UNKNOWN/执行中操作和最多三个有长度限制的 checkpoint 摘要。详细上下文按摘要中的 source 再读取。

上传使用 `UPLOAD_INTENT_LEASE`，要求任务 ID 和产物 SHA，并同时提示浏览器会话租约；浏览器使用 `BROWSER_SESSION_LEASE`；其他写入包括支付使用现有通用 `TASK_WRITE_LEASE`。支付类别仅建议串行化，未提供支付执行器或支付授权。

## 保持原会话，按需读取上下文

路由读取会话绑定；可续接时返回 RESUME_EXISTING，仅有会话 ID 时返回 VERIFY_ADAPTER_RESUME。没有绑定的当前 agent 返回 USE_CURRENT_SESSION，可在当前聊天继续工作；这不证明其他 agent 能远程唤醒它。唤醒目标无绑定时给出阻塞项。不支持 resume 的宿主先形成 digest 和 checkpoint 交接，不会因为安装 CLI 就获得 resume 能力。

建议一次接力只提供当前目标、owner、最新产物/证据位置、未决问题及下一步。历史细节保存在文件中，遇到对应问题再读取。普通消息只更新协作信息，显式 wake 才进入唤醒请求流程；没有新事件时无需循环调用模型。

不能运行 CLI 的 agent 先读工作区 `AEP_JOIN.md` 和可用状态文件，用自己的语言报告已知事实和能力限制。由具备命令能力的 agent 完成认领、租约和唤醒；file-only 参与者不能把“读过说明”报告成“已取得任务或资源”。

开发需求、验收范围及迭代记录见[开发计划](ROUTE_DEVELOPMENT_PLAN.zh-CN.md)。
