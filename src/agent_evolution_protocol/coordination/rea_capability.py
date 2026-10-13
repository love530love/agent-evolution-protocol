"""Idempotently announce a reviewed REA capability note to local mailboxes."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from . import bridge


def announce(bridge_root: Path, document: Path) -> dict:
    document = Path(document).resolve()
    if not document.is_file():
        raise ValueError("REA capability document missing")
    event_id = "REA-CAP-" + hashlib.sha256(document.read_bytes()).hexdigest()[:12]
    body = ("REA 能力入口已更新。只有涉及已授权制品/二进制/运行行为证据时使用；"
            "普通源码问题先读源码。先查已有证据，区分【实】【源】【推】。"
            "Codex/Hermes 注册需重连后验收；WorkBuddy/Qoder 可先查 rea.cmd CLI。"
            "不得把消息已读当任务 ACK，也不要静默轮询或自动升级。\n"
            f"请读取：{document}")
    sent = []
    for agent in bridge.AGENTS:
        path = Path(bridge_root) / "messages" / agent / f"{event_id}.json"
        if path.exists():
            continue
        bridge.write(path, {"id": event_id, "at": bridge.stamp(), "sender": "rea_capability",
                            "to": agent, "topic": "rea-evidence-capability", "kind": "context",
                            "text": body})
        sent.append(agent)
    return {"event_id": event_id, "new_mailboxes": sent,
            "note": "Mailbox delivery is neither read receipt nor agent ACK."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--document", type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(announce(args.bridge_root, args.document), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
