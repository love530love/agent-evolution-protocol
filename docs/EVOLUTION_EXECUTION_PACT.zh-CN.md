# 团队使命与自进化执行契约 v1.0

归属：红皇后—鲶鱼—创造性破坏。正确性、合规、授权为硬门槛；随后优化成果、成本和交付时间。

## 所有接入者应遵守

1. 明确主责、验收、预算、产物和停损条件。复杂任务使用现有 `coord-reasoning create` 冻结契约；声明授权不等于已获授权。
2. 按实测能力分工；可独立交付且并行收益明显时使用子 Agent。不存在可用启动接口时不得声称已唤醒。
3. 事实、报错和致命风险及时共享；候选方案独立形成后交叉评审，避免先入为主与集体趋同。
4. 同题允许差异化路线竞争，不因队友已认领而自动退让；共享文件、桌面与上传使用既有资源锁。
5. 红皇后依据真实外部变化提出有预算挑战；鲶鱼提供替代假设与最低成本证伪；创造性破坏按实验与回归替换实现，不按权威或投票裁决。
6. 每轮形成可用产物、证伪结论、根因或明确阻塞。相同失败无新增证据不得重复消耗额度；非相关路线继续推进。
7. 奖励可复现突破、有效排雷、实际复用与补位，不奖励消息数量、唤醒次数或虚报完成。诚实失败不罚；贡献仍经现有独立裁定流程计分。
8. 完成、失败和关键证据触发接力；静默期不消耗模型额度轮询。没有后台执行机制时如实记录待接力，不承诺自动继续。

## 新增可执行改进流水线

`coord-improvement` 提供本地不可覆盖的序列日志及哈希链：

PROPOSED → VALIDATED → REVIEWED → TRIAL → ADOPTED → ROLLED_BACK。

前三阶段也可 REJECTED；试运行可直接 ROLLED_BACK。不得跳级。REVIEWED 的声明身份必须不同于作者和验证者。每次转换需要 actor、note、evidence；证据文件冻结 SHA-256，变动阻止晋级，但不阻止记录紧急回滚。

```powershell
aep --coord-root coordination coord-improvement propose --request-file proposal.json
aep --coord-root coordination coord-improvement status --id <返回的24位ID>
aep --coord-root coordination coord-improvement advance --id <ID> --state VALIDATED --actor tester --note '隔离测试通过' --evidence test-results.txt
```

proposal.json 必须包含 author/change/acceptance/rollback/scope（非空字符串）及 evidence（实际证据文件路径列表）。可将现有 evolution_gate 的基线比较结果作为证据文件，现有任务契约作为 scope 的来源。

边界：这是可执行的流程与证据记录，不自动运行测试、不判定证据真伪、不认证 Agent 身份、不修改生产配置。ADOPTED 是记录的采纳决定，不代表实际部署。文件哈希不是数字签名；同机文件修改者仍有篡改能力。权限扩张、付费资源与合规边界改变仍需明确授权。该流程不会替换已有上传门、租约或实验晋级门。

## 开发顺序

### 验证失败也必须交接（2026-10-23）

已实现[验证结果接力](VALIDATION_HANDOFF.zh-CN.md)：真实测试的成功、失败和晋级受阻均生成冻结回执；接替者 brief 获取当前阶段结果。单提案执行锁避免重复启动，未完成运行阻止自动认领/重跑；证据哈希结束后再次检查租约与阶段。self-test 新增真实失败后脚本角色交接及显式修复。恢复入口、进程树停止验证和真实宿主 ACK 仍待实现，不能将记录可读当作执行已停止。

### 一键集成验收（不是多 Agent 实测）

`aep --coord-root <验收产物父目录> coord-improvement self-test` 在 acceptance_runs/唯一ID 下创建全新工作区和协作区，不改变父目录的生产策略或订单。串联事件排队/去重、强制策略、认领与简报、真实 unittest 子进程、独立身份约束、脚本角色推进及回滚记录，保存 acceptance-report.json、测试输出与完整阶段日志。失败返回 CLI 2，不自动清理证据或重试。

报告的 actual_test_process 表示确实运行测试；scripted_roles=true 明确说明复核/试运行/采纳是脚本 API 演练，不是真实独立 Agent 作出技术判断。desktop_delivery_verified、agent_ack_verified、deployment_performed 均为 false。此验收不等于原会话双向投递、全局取消、真实部署或无人值守作战验收。

CI 扩展至 Ubuntu/Windows × Python 3.10/3.12，分别跑单测、pytest 和上述集成验收；fail-fast=false 保留各平台失败证据。工作流已配置不代表远程结果已通过，发布时必须再核对。

### 日志原子发布与崩溃恢复加固

提案阶段日志和事件去重索引改为同目录临时文件完整写入、flush/fsync，然后以不覆盖目标的硬链接原子发布。并发相同提案只有一个首事件，其他调用读取并验证已发布结果；推进冲突明确失败，不覆盖胜出者。事件 ingest 使用独占 OS 锁串行，避免同一事件 ID 并发写入产生不同结果。

发布前写入或刷新失败不产生正式 JSON；遗留空提案目录可在下次 propose 恢复。异常退出遗留的 .tmp 不参与读取，不自动删除人工资料。既有损坏正式日志仍拒绝执行，不能凭空修复或覆盖证据。

要求支持同卷硬链接的文件系统（Windows 默认 NTFS）；不支持时明确失败，不偷偷改用覆盖写。此机制降低半写文件风险，不代表断电持久性、跨机器事务、恶意篡改防护或全局回滚保证。故障注入测试是模拟系统调用失败，不应宣称已进行真实断电测试。

### 可选强制接力模式

`coord-improvement policy` 只读当前模式；默认 cooperative，兼容原调用。

```powershell
aep --coord-root coordination coord-improvement policy --mode enforced --actor operator --note '完成接力客户端迁移后启用'
# 回退兼容模式也必须显式记录原因
aep --coord-root coordination coord-improvement policy --mode cooperative --actor operator --note '兼容性恢复'
```

enforced 在该协作目录内要求正常晋级（VALIDATED/REVIEWED/TRIAL/ADOPTED）提供有效 token，并匹配持有者、到期时间、日志头和目标阶段；旧 advance 也不能省略。validate 无 token 在启动测试前就拒绝。complete 会传递并校验 token。REJECTED/ROLLED_BACK 保留无需 token 的停损通道，但仍要求有效阶段、证据与决策说明。

模式与切换历史保存在 improvement_policy.json，损坏配置拒绝读取和覆盖，不默认为兼容模式。此机制防止意外绕过，不是身份认证、安全隔离或操作系统权限边界；有本地写权限者仍可修改策略文件，声明 actor 不等于认证管理员。策略切换与运行中测试并非全局原子事务；启用前先停止相关执行并确认客户端已迁移。测试通过也不能证明试运行或实际部署已完成。

本轮仅在临时测试目录验证强制模式，未修改生产 coordination 的模式，也未改变其他 Agent 当前行为。

### 认领后的执行简报、续租与结果围栏

`coord-improvement brief --id <ID> --actor <成员> --token <token>` 检查有效租约、当前日志头和全部既有证据 SHA-256，返回目标、验收、作用范围、回滚说明及所需交付。证据缺失/变动立即拒绝；文本是待处理数据，不是新的授权，不自动读取全文或执行其中指令。

`renew --id <ID> --actor <成员> --token <token> --ttl 300` 仅允许尚未到期的同一持有者续租，不能复活过期 token。总期限固定不超过首次认领后一小时，不得借续租扩大任务预算；旧版本没有 deadline 的租约必须完成或释放后重新认领。当前只保留最近续租状态，完整租约历史待开发。

`validate --id <ID> --workspace <可信目录> --actor <成员> --token <token>` 在运行测试前调用简报校验，并在测试通过后通过 finish 再核对租约。运行期间到期、被补位或阶段变更时，不因测试成功而强行晋级，返回 PROMOTION_BLOCKED（CLI 2），原始测试报告不改写，另写 promotion.json 说明。检查实际状态后再决定下一步，不盲重跑。

无 token 的旧 validate 和 advance 仍保留，尚未成为全系统强制门；续租不是执行安全沙箱或资源授权。租约失效不会中途终止正在运行的测试，只阻止带 token 的结果提交。并发手工 advance 仍可能导致部分提交，错误时应核对日志，不自动宣称回滚成功。

### 过时订单的受控退役

`coord-improvement retire --order-id <订单ID> --actor operator --note '阶段已推进，未开始输入'` 将已过时且当前状态及全部历史均为 DEPOSITED/DEFERRED 的订单标记 RETIRED，保留订单与审计回执并释放路由名额。当前阶段订单不允许退役；只要历史出现 WINDOW_FOUND、INPUT_SUBMITTED、MESSAGE_VISIBLE、CEASED 等状态就拒绝，未知送达必须另行核查。

RETIRED 订单不会被 tick、ACK 扫描、reinforce 或直接 dispatch 再启动；dispatch 会重新读取持久退役状态，旧内存副本也不能重发。改进订单投递与退役使用同一 OS 写锁串行，避免退役同时输入；锁冲突明确报错，不自动重试。

这不是消息撤回或全局执行取消。提案状态推进仍有自身的并发边界，其他未使用该锁的旧写入接口不构成完全事务。退役为显式操作，不自动批量清理生产订单。

### 送达、ACK、认领与阶段变化的关联

`coord-improvement delivery-status` 只读关联 commander 订单、改进日志和有效认领，区分 AWAITING_ACK、ACK_WITHOUT_CLAIM、CLAIMED、STAGE_CHANGED、DELIVERY_BLOCKED 与 DAMAGED，并给出下一步。它不读取屏幕，不采集新的 ACK；ack_recorded 只是既有指挥记录。STAGE_CHANGED 可能是完成、拒绝或回滚，不能一概当成功。

commander.dispatch 在发现窗口或输入之前重新核对 improvement 订单所绑定的阶段日志头：过时或不存在的提案转为 CEASED，禁止输入旧指令。此门仅用于 improvement: 订单，不改变普通任务语义；不是已发送消息的撤回，也不能消除检查后状态变化的全部并发竞态。执行者仍须在 claim/complete 时再次核验。跨进程阶段变更与桌面副作用的统一围栏仍待开发。

### 能力路由接入既有指挥队列

`coord-improvement schedule --request-file roster.json` 默认只生成匹配计划；加 `--apply --limit 1` 才写入既有 commander 指令信箱。roster 格式见 examples/improvement-roster.json，必须替换为真实成员与已核实能力；动作支持 validate/review/trial/assess。读取 presence 的新鲜 online 状态，排除已有 campaign 活跃任务、当前认领中的工作以及非独立复核者。能力名单仍是声明，不是能力认证。

同一提案日志头产生稳定事件 ID，队列已有此 ID 就不重复生成，包括未知送达、失败和已 ACK 情况。只生成原会话指令，allow_launch=false，无备份自动转投，不直接调用桌面或模型。既有 commander 的投递与 ACK 流程负责后续验证；必须在真实目标会话验收后才可声称送达。

监听器可显式加 `-RosterFile <名单绝对路径>`，每次接入/本地恢复扫描后最多排队一项，静默时不输出重复计划。默认不调度。未安装 commander 自动投递服务，排队不等于已发送。接力消息要求收件人自行检查契约、认领和回复 ACK，不把在线或收到消息当作完成。

保守停损：尚未 ACK 的改进订单会占用对应队友路由名额，即使订单失败也不会偷偷换人重发。已 ACK 但阶段未变也保持占用，确认阶段已推进才释放该订单名额；缺失/损坏进度不推断完成。需要人工或已有指挥流程核实旧订单；未实现过期订单自动撤销和下游执行取消。当前路由是保守低并发调度，不是完备全局调度器。

### 接力工作项与租约

`coord-improvement pending` 从提案状态派生待验证、待独立复核、待试运行和待评估工作，无须重复创建任务。`claim --id <ID> --actor <成员> --ttl 300` 认领返回 token；同阶段有效租约拒绝第二次认领，过期后允许补位。

```powershell
aep --coord-root coordination coord-improvement pending
aep --coord-root coordination coord-improvement claim --id <ID> --actor reviewer --ttl 300
aep --coord-root coordination coord-improvement complete --id <ID> --actor reviewer --token <返回token> --note '独立复核结论' --evidence review.txt
# 主动让出，不推进状态：complete 改为 release，无需 note/evidence
```

完成时检查 token、声明身份、到期时间与认领时日志头；过期工作者或状态已变化的工作者不得推进。复核者不能是作者或已有验证者。租约仅协调这套接力入口，不代表身份认证，也不是进程执行权限或跨机器分布式锁。

使用 `validate` 执行实际测试时，它会自行推进状态；原验证租约随日志头变化失效，不要再 complete。下一名队友可直接认领 review。手工 complete 属于有证据的结果声明，不替代 validate 的真实执行，不校验证据内容真伪。旧 advance 接口仍保留，并不强制要求接力租约；当前用于合作式调度，不可宣称已全局强制围栏。每个 claim 的当前记录保留上一个 token，尚无完整租约事件审计或自动续租。

未安装 Agent 自动接单器；pending/claim 是已在线队友可调用的接口，不表示已唤醒或消息已达。自主分发、实际 ACK 和认证能力匹配仍待接入。

### 持久信箱与事件监听器

生产者用 `aep --coord-root coordination coord-improvement enqueue --request-file event.json` 写入持久信箱。使用原子文件替换及单写锁；同一事件 ID 内容变化拒绝覆盖。生产者负责把证据写入稳定位置，再投递事件。

消费者运行：

```powershell
./scripts/watch-improvements.ps1 -Python <已安装当前AEP的python.exe> -CoordRoot coordination
# 一次性恢复积压，不常驻：加 -Once
```

Windows 文件事件触发 `drain`；启动时及每 60 秒本地恢复扫描补偿丢失通知，不调用模型或网络 API，未变化不输出。处理回执在 improvement_spool/receipts，原事件保留供证据引用；错误事件记录 REJECTED，不自动重试、不阻塞其他事件。修正后需新事件 ID；人工确认恢复时可处理对应回执。不要改写已被提案引用的证据文件。

回执丢失后重启会重放，已有事件去重避免重复提案。单写锁避免多个消费者同时处理；锁冲突会明确失败，监听器停止，不产生隐藏重试风暴。当前是显式启动的本机进程，不自动安装开机服务；未运行时信箱持久积压，下次启动恢复。该监听器仅创建提案，不执行测试、不发聊天消息、不部署。CLI ingest/drain 仍有原接口，供其他 Agent 或编排器主动调用。

### 状态与停止（2026-10-18 加固）

`aep --coord-root coordination coord-improvement overview` 只读列出 pending/processed/rejected/damaged 队列数量、提案状态和下一动作。处理回执不是对方 Agent ACK，状态入口不会声称监听器在线。

监听器现在持有独占 OS 文件锁，同一协作目录不允许两个监听器并行常驻，异常退出由 OS 释放。运行 `./scripts/watch-improvements.ps1 -Python <python.exe> -CoordRoot coordination -Stop` 写入停止标记；当前 drain 完成后退出，通知丢失时最迟下一次 60 秒扫描识别。标记保留，禁止自动重启；检查状态后显式移除 `improvement_spool/inbox/.stop` 才能重新启动。仅停止监听，不撤回已创建提案，也不终止其他 Agent。

损坏回执保存到 damaged_receipts 后重新接入，单条错误不阻塞其他事件。回执、原始事件和证据均应按本地敏感资料管理；这仍不提供跨用户身份认证或防恶意篡改。

### 已落地的事件接入与真实验证

`coord-improvement ingest --request-file event.json` 接收显式事件，字段为 event_id、kind（TASK_COMPLETED/TASK_FAILED）、improvement（上述提案对象）。同一 event_id 相同内容复用提案，内容改变拒绝重放；不会从日志文字推断要执行的命令。

`coord-improvement validate --id <ID> --workspace <可信项目目录> --actor tester --timeout 120` 实际执行当前 Python 的 `unittest discover -s tests -v`，记录输出、退出码、测试数量和报告。成功且至少执行一个测试才进入 VALIDATED；失败保留 PROPOSED，CLI 返回 2。不自动复核、采纳或部署。

重要：这是有时限的子进程，不是安全沙箱。运行测试本身会执行项目代码，必须信任并授权目标目录。超时终止直接子进程，不保证回收其创建的全部后代；网络、文件写入和 CPU/内存隔离尚未实现。事件投递仍须调用 ingest，未安装常驻监听器，不产生静默模型轮询。

- 本轮：正式契约、CLI 状态门、证据冻结、独立身份约束和回滚记录。
- 下一步：任务完成事件生成去重改进候选；沙箱自动测试与证据生产。
- 后续：认证评审、局部试运行指标、实际部署/回滚适配器及 ACK；干净机器验证。

自动产生提案不等于自动批准。策略与实现可变，授权边界与证据要求不能由候选自己取消。
