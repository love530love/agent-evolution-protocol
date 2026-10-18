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

### 持久信箱与事件监听器

生产者用 `aep --coord-root coordination coord-improvement enqueue --request-file event.json` 写入持久信箱。使用原子文件替换及单写锁；同一事件 ID 内容变化拒绝覆盖。生产者负责把证据写入稳定位置，再投递事件。

消费者运行：

```powershell
./scripts/watch-improvements.ps1 -Python <已安装当前AEP的python.exe> -CoordRoot coordination
# 一次性恢复积压，不常驻：加 -Once
```

Windows 文件事件触发 `drain`；启动时及每 60 秒本地恢复扫描补偿丢失通知，不调用模型或网络 API，未变化不输出。处理回执在 improvement_spool/receipts，原事件保留供证据引用；错误事件记录 REJECTED，不自动重试、不阻塞其他事件。修正后需新事件 ID；人工确认恢复时可处理对应回执。不要改写已被提案引用的证据文件。

回执丢失后重启会重放，已有事件去重避免重复提案。单写锁避免多个消费者同时处理；锁冲突会明确失败，监听器停止，不产生隐藏重试风暴。当前是显式启动的本机进程，不自动安装开机服务；未运行时信箱持久积压，下次启动恢复。该监听器仅创建提案，不执行测试、不发聊天消息、不部署。CLI ingest/drain 仍有原接口，供其他 Agent 或编排器主动调用。

### 已落地的事件接入与真实验证

`coord-improvement ingest --request-file event.json` 接收显式事件，字段为 event_id、kind（TASK_COMPLETED/TASK_FAILED）、improvement（上述提案对象）。同一 event_id 相同内容复用提案，内容改变拒绝重放；不会从日志文字推断要执行的命令。

`coord-improvement validate --id <ID> --workspace <可信项目目录> --actor tester --timeout 120` 实际执行当前 Python 的 `unittest discover -s tests -v`，记录输出、退出码、测试数量和报告。成功且至少执行一个测试才进入 VALIDATED；失败保留 PROPOSED，CLI 返回 2。不自动复核、采纳或部署。

重要：这是有时限的子进程，不是安全沙箱。运行测试本身会执行项目代码，必须信任并授权目标目录。超时终止直接子进程，不保证回收其创建的全部后代；网络、文件写入和 CPU/内存隔离尚未实现。事件投递仍须调用 ingest，未安装常驻监听器，不产生静默模型轮询。

- 本轮：正式契约、CLI 状态门、证据冻结、独立身份约束和回滚记录。
- 下一步：任务完成事件生成去重改进候选；沙箱自动测试与证据生产。
- 后续：认证评审、局部试运行指标、实际部署/回滚适配器及 ACK；干净机器验证。

自动产生提案不等于自动批准。策略与实现可变，授权边界与证据要求不能由候选自己取消。
