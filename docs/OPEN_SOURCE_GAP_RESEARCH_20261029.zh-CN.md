# 开源能力补缺调研

## 结论

当前不应把 AEP 整体替换成重型平台，而应采用“核心协议轻量化 + 可选适配器”的方式吸收成熟项目。

## 推荐吸收

### Temporal：可选持久化执行后端

Temporal 的 Durable Execution 能让工作流在崩溃、超时和服务重启后继续推进，并支持消息、定时器、并行和人工介入。它与 AEP 的 mission graph、wake、retry、human-in-the-loop 方向高度匹配。第一阶段只借鉴 workflow/activity/signal/retry 语义；第二阶段再提供可选 Temporal adapter，不能让核心安装强依赖服务端。

### OpenTelemetry：统一可观测性

将唤醒、窗口锚定、provider 调用、readback、ACK、重试和任务图转换映射为 trace/span/metric/log，可跨 Agent 和跨机器追踪一条任务链。核心仍保留 JSONL 离线日志，OpenTelemetry 作为可选导出器。

### watchdog：文件事件唤醒

文件信箱、任务图和回执目录可使用 watchdog 监听文件创建/修改事件，替代静默期间的固定间隔轮询。没有 watchdog 时回退到现有有界 watch。

### Playwright/CDP：浏览器验收适配器

Playwright 适合建立浏览器页面、定位器、截图、网络和回读的 task-level acceptance；CDP 继续作为低层通道。二者都必须输出统一 `aep-readback-v1`，不能把点击 API 返回值当成成功。

## 暂不直接引入

- 重型全家桶编排平台：会增加部署、版本和迁移成本；
- 未经任务级验收的 Computer-use 项目：工具发现不代表原会话安全；
- 只提供模型调用的 Agent 框架：不能替代 AEP 的会话身份、租约、证据和接力协议。

## 落地路线

1. 先实现 OpenTelemetry exporter 和 watchdog optional extra；
2. 为 Playwright/CDP 写一个真实浏览器 acceptance fixture；
3. 将 mission graph 映射到 Temporal adapter，保持核心离线可用；
4. 用同一组故障注入测试比较内置执行器和可选后端。

参考：[Temporal Durable Execution](https://docs.temporal.io/temporal)、[Temporal Workflows](https://docs.temporal.io/workflows)、[Temporal GitHub 文档](https://github.com/temporalio/documentation)。
