# ClawChat/Cowchat 可选传输适配器

适配器位于 `coordination/clawchat_transport.py`，使用 Python 标准库 TCP + NDJSON，不增加硬依赖。

```python
from agent_evolution_protocol.coordination.clawchat_transport import *

msg = envelope(event_id="EVT-1", sender="aep", target="qoder_cn",
               room="flaggems", task="status", text="请回报战况")
print(send(ClawChatConfig(port=7310), msg))            # PREVIEW
print(send(ClawChatConfig(port=7310), msg, execute=True))  # 显式发送
```

适配器不会自动重连、重发或替代 ACK。发送回执仍需写入 AEP 审计，并由目标 Agent 返回带事件 ID 的 `AGENT_ACK`。Cowchat 不可用时，继续使用本地总线、UIA 或 CDP。
