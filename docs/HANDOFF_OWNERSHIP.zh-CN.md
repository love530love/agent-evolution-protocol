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
