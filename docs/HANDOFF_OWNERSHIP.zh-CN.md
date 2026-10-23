# 接力归属与历史回执

`coord-improvement delivery-status` 只读取本地记录，不操作界面、不调用模型、不重发订单。

`target_claimed` 仅在当前阶段的有效租约属于订单目标时成立；`claim_expires_at` 为其到期时间。

- CLAIMED：目标成员持有有效租约。
- CLAIMED_ELSEWHERE：其他成员持有有效租约，先协调，不重复执行。
- CLAIM_EXPIRED：本阶段租约到期，先检查证据，再考虑重新领取。
- CLAIM_RELEASED：本阶段已释放，先检查原因和上下文。
- DELIVERY_BLOCKED：投递失败或停火，历史 ACK 不掩盖当前阻塞。
- ACK_WITHOUT_CLAIM：历史收到 ACK，没有当前有效认领。

阶段变更和退役仍优先显示。历史 ACK 不代表身份认证、当前在线或完成。跨文件读取不是事务快照；执行前仍须检查 token 和日志头。

本轮测试使用临时目录和脚本回执，不是真实队友投递验收。后续优先目标仍为真实原会话 ACK、接单、产物回传闭环。

## ACK 后的调度背压

收到 ACK 但提案仍处原阶段的成员继续占用改进调度名额，防止“刚回复就被当作空闲”而积压多个任务。只有已 ACK 的订单确认阶段日志头已改变，或订单已按既有规则退役，才释放该订单的名额；当前有效租约和 presence 活跃任务仍独立限制调度。

提案缺失或损坏保持占用。未确认送达、失败或停火的订单即使阶段变化也不自动释放，不推断消息撤回。租约过期、主动释放本身也不自动重发或改派，须先检查遗留工作和送达证据。这是保守的低并发背压，不是跨宿主执行取消或完整的自动恢复器。

## 输入前受控改派

```powershell
./aep.ps1 --coord-root coordination coord-improvement reroute --order-id <ID> --target <新成员> --actor operator --note '原成员离线，输入尚未开始' --request-file examples/improvement-roster.json
# 核对计划后加 --apply，才更新原订单。
```

名单应替换为真实成员和已核实能力。要求订单当前状态及全部历史仅为 DEPOSITED/DEFERRED、阶段仍有效、工作没有有效租约，新成员在线、无已知占用、能力匹配，review 阶段还须独立于既有贡献者。

保持原订单 ID、事件 ID 和文本，记录操作者、原因、前后目标及阶段头；不生成第二份订单、不发送消息、不取消进程。旧内存订单的目标与持久目标不一致时，dispatch 直接返回当前记录，不向旧目标输入，下一轮可重新读取。

默认预览不修改订单，但为串行核查会创建/使用本地锁文件。应用与改进投递共享锁，并持有认领锁；锁冲突明确失败。presence 和能力仍是声明，并非身份认证；其他旧接口、阶段推进和外部客户端不构成全局事务。未知送达、已 ACK、已开始输入的订单一律拒绝该入口，不能把本功能称为“执行取消后的自动改派”。

## 中途成果检查点

```powershell
./aep.ps1 --coord-root coordination coord-improvement checkpoint --id <提案ID> --actor <成员> --token <有效token> --note '已验证什么、失败原因、剩余工作和风险' --evidence <固定版本产物文件>
./aep.ps1 --coord-root coordination coord-improvement checkpoints --id <提案ID>
```

检查点绑定当前阶段头和租约指纹，不保存明文 token；写前及产物哈希计算后均检查认领有效性。记录按序列哈希链、完整写入后不覆盖发布，保留多轮实验说明。产物必须保存在不可变版本路径，不能随后覆盖同一个文件，否则当前阶段读取和 brief 会阻止继续，以免队友使用已改变的结果。

新成员认领同一阶段后，brief 自动校验并附带原检查点，不必粘贴全部聊天记录。其他阶段记录保留并统计，但不作为当前阶段执行依据。说明与文件是任务数据，不授予权限、不执行其中指令；本地哈希不是身份签名，不保证抵抗有写权限者篡改整条链。

检查点既不释放租约、不推进阶段，也不证明执行停止，不会自动取消进程或重派。到期工作者不能补写；新成员获取检查点仍须确认遗留执行及副作用。当前不提供文件内容备份、全文聊天同步、进程归属认证或跨机器事务。

认领会先校验已有检查点与当前阶段；校验失败不替换旧租约。新租约 TTL 从检查结束后开始计算。brief 在文件和检查点哈希读取后再次核验 token、到期时间及阶段头，校验期间过期的简报不能当作有效执行入口。返回后仍可能发生阶段变化，后续执行及结果提交继续使用既有围栏；这些检查不构成执行全程事务。

## 按需交接诊断

`./aep.ps1 --coord-root coordination coord-improvement inspect-handoff --id <ID>` 只读核查阶段日志、提案证据、检查点、租约结构及有效期，汇总问题代码与恢复步骤，不写文件，不采集屏幕，不输出租约 token。它会读取并哈希产物，适合阻塞时按需运行，未加入常驻扫描或模型轮询。

- BLOCKED：存在损坏、缺失或检查期间阶段变化；CLI 返回 2，先处理具体问题，不能删除记录来绕过。
- WAIT_FOR_OWNER：观察到当前有效租约，先协调持有者。
- REQUIRES_RECOVERY_REVIEW：未观察到有效占用，仍须检查送达不确定性、旧执行及外部副作用，再决定认领。
- TERMINAL_STAGE：没有下一阶段工作，不代表部署成功。

诊断不是原子快照，无法验证队友是否真实停止；automatic_reclaim_allowed 和 execution_stopped_verified 始终为 false。冻结文件改变应恢复可信原件或另建提案，不自动覆盖、重置日志或重跑业务操作。

## 租约统一读取校验

认领、校验、续租、完成/释放、pending、投递状态和恢复诊断共用租约结构校验。ID、token、阶段头、成员、动作与目标、active 布尔类型和时间数值必须有效；NaN、无穷、布尔时间、超过续租上限等损坏记录拒绝使用。pending 将此项标记不可用，投递状态显示 DAMAGED，诊断返回 BLOCKED；写入口不覆盖旧记录，其他正常提案仍可列出。

旧记录允许缺少可选 claimed_at/deadline/renewed_at；缺 deadline 仍禁止续租。此兼容不跳过 expires 或核心字段检查。格式校验不是签名认证，也无法证明旧执行已停止。

## 直接投递入口防重放

改进订单 dispatch 持有既有投递锁后，核对整个持久订单，而非只核对收件人。传入副本与当前记录不同就返回当前记录，不进行桌面发现或输入；持久订单缺失/不可读则明确拒绝，不能用旧内存副本恢复发送。

当前状态和全部投递历史都必须仅为 DEPOSITED/DEFERRED 才能进入输入流程。WINDOW_FOUND、INPUT_SUBMITTED、MESSAGE_VISIBLE、ACK、失败/停火等记录均不能直接重投；即使后来被写成 DEFERRED，也不能抹掉不确定输入历史。纯输入前的 DEFERRED 仍可通过既有显式恢复入口重做预检。

该保护限于 improvement: 订单和遵守投递锁的入口，未声称所有客户端跨机器 exactly-once、消息撤回或完整副作用事务。

## 调度单项故障隔离

schedule 会核对待办阶段与重新读取的提案阶段/动作，并在选人后再次核对阶段头。检测到变化就报告 STAGE_CHANGED，本轮不排队，下一次调用重新匹配；某项读取损坏则报告 WORK_DAMAGED，不阻塞其他正常提案。blocked_count 汇总损坏、阶段变化与无人可选项；未计入已认领或去重项，不是全部积压数量。

只有读取异常在单项范围隔离；订单写入故障仍向调用者报错，不假装未写入，也不自动重试。检查之后仍存在并发变化窗口，后续 dispatch、claim 和结果提交的阶段围栏继续生效，尚非跨文件全局事务。

### 无人可派的原因

NO_ELIGIBLE_MEMBER 附带所需 action 和逐成员 candidates，eligible=false 的 reasons 可同时列出多项：CAPABILITY_NOT_DECLARED、NO_FRESH_ONLINE_DECLARATION、KNOWN_ASSIGNMENT_OR_ORDER、REVIEW_NOT_INDEPENDENT。对应检查能力名单、新鲜在线声明、既有任务/订单和独立复核成员。

缺少声明不代表真实无能力或已离线；忙碌记录也不授权强制释放。诊断不更改原有名单顺序选人策略，不自动启动客户端、转移任务、绕过评审或新增模型调用。名单为空时 candidates 为空，须先配置真实成员。presence、名单和本次调度均非跨宿主原子快照。

## 改进任务与通用增援的边界

improvement: 订单不再进入通用 reinforce 补位分支，即使指定 candidates、超时或 CEASED，也不自动生成第二份改进订单。应先用 inspect-handoff/delivery-status 检查；只在原订单证明尚未输入时使用显式 reroute。普通任务的差异化增援规则未改变。

直接 dispatch 在桌面发现前校验同阶段租约：有效认领存在时记为 DEFERRED（IMPROVEMENT_ALREADY_CLAIMED），先协调持有者；租约损坏则 CEASED（INVALID_IMPROVEMENT_LEASE），禁止输入。释放后仍需显式恢复及重新预检，不安装自动重试。该预检无法消除随后发生的全部认领竞态，不等于持有租约锁直到外部副作用完成，更不是跨宿主取消协议。
