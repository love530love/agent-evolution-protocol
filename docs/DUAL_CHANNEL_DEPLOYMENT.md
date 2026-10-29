# 双通道验证部署状态

代码入口：`coordination/dual_channel.py`。

适配器在得到协议和 GUI 证据后调用 `evaluate(...)`，不得凭单一协议返回值宣布唤醒。当前状态评估器已通过四个场景测试：协议黑箱、GUI 已投递待 ACK、双侧可见、结果已核验。

下一步接入点是 commander 的每次投递回执：协议 transport 写入 `protocol_sent/protocol_ack`，UIA/CDP 写入 `gui_session/gui_visible/gui_executing`，外部回表写入 `result_verified`。
