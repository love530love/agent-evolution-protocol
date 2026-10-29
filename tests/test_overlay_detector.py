from agent_evolution_protocol.coordination.overlay_detector import classify, recommended_action


def test_error_alert_requires_authorized_retry():
    overlay = classify(control_type="Alert", title="这轮回复失败", text="执行连接发生异常")
    assert overlay.kind.value == "alert"
    assert recommended_action(overlay) == "pause-and-report"
    assert recommended_action(overlay, authorized=True) == "retry-if-idempotent"


def test_popconfirm_is_not_auto_accepted():
    overlay = classify(role="dialog", title="确认提交", actions=["取消", "确定"])
    assert overlay.kind.value == "popconfirm"
    assert recommended_action(overlay) == "wait-user-confirmation"


def test_toast_is_record_only():
    overlay = classify(role="notification", text="已保存")
    assert overlay.kind.value == "toast"
    assert recommended_action(overlay) == "record-only"
