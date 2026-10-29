"""Classify desktop/browser overlays without performing an action."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class OverlayType(str, Enum):
    TOOLTIP = "tooltip"
    TOAST = "toast"
    ALERT = "alert"
    MODAL = "modal"
    POPCONFIRM = "popconfirm"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Overlay:
    kind: OverlayType
    title: str = ""
    text: str = ""
    actions: tuple[str, ...] = ()
    source: str = "uia"
    risk: str = "observe"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        value["actions"] = list(self.actions)
        return value


def classify(*, control_type: str = "", role: str = "", title: str = "",
             text: str = "", actions: list[str] | tuple[str, ...] = (),
             source: str = "uia") -> Overlay:
    """Classify one observed overlay; never clicks or dismisses it."""
    blob = " ".join((control_type, role, title, text)).lower()
    action_tuple = tuple(str(a).strip() for a in actions if str(a).strip())
    if any(x in blob for x in ("tooltip", "tool tip", "提示")):
        kind, risk = OverlayType.TOOLTIP, "observe"
    elif any(x in blob for x in ("toast", "notification", "通知", "已保存", "成功")):
        kind, risk = OverlayType.TOAST, "observe"
    elif any(x in blob for x in ("popconfirm", "are you sure", "确认", "确定要")):
        kind, risk = OverlayType.POPCONFIRM, "confirm-required"
    elif any(x in blob for x in ("alert", "警报", "错误", "失败")):
        kind, risk = OverlayType.ALERT, "pause-or-retry"
    elif any(x in blob for x in ("modal", "dialog", "对话框", "弹窗")) or action_tuple:
        kind, risk = OverlayType.MODAL, "block-and-assess"
    else:
        kind, risk = OverlayType.UNKNOWN, "observe"
    return Overlay(kind, title.strip(), text.strip(), action_tuple, source, risk)


def recommended_action(overlay: Overlay, *, authorized: bool = False) -> str:
    if overlay.kind in (OverlayType.TOOLTIP, OverlayType.TOAST):
        return "record-only"
    if overlay.kind == OverlayType.POPCONFIRM:
        return "allow-confirmed-action" if authorized else "wait-user-confirmation"
    if overlay.kind == OverlayType.ALERT:
        return "retry-if-idempotent" if authorized else "pause-and-report"
    if overlay.kind == OverlayType.MODAL:
        return "proceed-with-lease" if authorized else "pause-and-report"
    return "observe-and-report"
