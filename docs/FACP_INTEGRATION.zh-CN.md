# FACP 联邦协议接入

来源：[love530love/FACP](https://github.com/love530love/FACP)，审查提交 `e99a7bd21a6fa049f0e0072494d395fc34401094`，FACP-002 v0.4.0。独立实现转换层，未复制上游执行器。

`python -m agent_evolution_protocol.coordination.facp --call call.json --binding binding.json`

将 COLLAB_CALL 转为严格 AEP dispatch envelope，保留 msg_id/trace_id/fsid 与原 payload。绑定文件显式指定 fsid、caller_node_id、target_node_id、caller_domain、target_domain、workspace_id、session_id、sender、target。同一UUID确定性映射同一EVT，沿用AEP幂等机制。绑定必须由宿主核验；此模块不提供身份认证。

当前支持同域、显式既有会话的 code_review/document_polish/deep_plan/session_summary。校验深度、TTL、暂停、目标和调用方绑定。跨域尚无审计令牌验证器，明确拒绝；desktop_execute与多模态请求需专用执行适配，不能当文本派工偷渡。不会隐式创建会话。

转换本身不发送。dispatch envelope后续交给既有AEP投递入口；reply保留关联，只在调用方提供已验证结果与证据引用时返回success。ACK、进程退出和投递可见不能直接映射完成。

待接线：持久FSID绑定注册表、NATS接收/回复、原订单派发及回执监听、跨域审计验证、Observer联合Trace。当前是可测试的协议边界，不是已运行的FACP联邦。
