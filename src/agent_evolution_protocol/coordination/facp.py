"""FACP-002 translation boundary; transport and execution stay with AEP."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

from agent_evolution_protocol.isotime import parse_iso
from .dispatch_contract import build


INTENTS = {'code_review', 'document_polish', 'deep_plan', 'session_summary'}


def translate(call: dict, binding: dict, *, now: datetime | None = None) -> dict:
    """Translate only explicitly bound sessions; no implicit chat creation."""
    if call.get('msg_type') != 'COLLAB_CALL':
        raise ValueError('expected COLLAB_CALL')
    for key in ('msg_id', 'trace_id', 'fsid', 'caller_node_id', 'target_node_id',
                'caller_domain', 'target_domain', 'created_at'):
        if not isinstance(call.get(key), str) or not call[key].strip():
            raise ValueError(f'missing {key}')
    identifier = UUID(call['msg_id'])
    if identifier.version != 4:
        raise ValueError('msg_id must be UUID v4')
    if call.get('intent') not in INTENTS:
        raise ValueError('intent requires a separate executor or modality adapter')
    if call['fsid'] != binding.get('fsid') or call['target_node_id'] != binding.get('target_node_id'):
        raise ValueError('federation session or target mismatch')
    for key in ('workspace_id', 'session_id', 'sender', 'target'):
        if not isinstance(binding.get(key), str) or not binding[key].strip():
            raise ValueError(f'explicit binding required: {key}')
    if binding.get('user_suspend'):
        raise ValueError('federation session suspended')
    # Declarations in an envelope are not authentication; trusted binding owns identity.
    if call['caller_node_id'] != binding.get('caller_node_id'):
        raise ValueError('caller not bound')
    if call['caller_domain'] != binding.get('caller_domain') or call['target_domain'] != binding.get('target_domain'):
        raise ValueError('domain identity mismatch')
    cross = call['caller_domain'] != call['target_domain']
    if call.get('cross_domain') is not cross:
        raise ValueError('cross_domain inconsistent')
    if cross:
        raise ValueError('cross-domain audit-token verifier not configured')
    depth, maximum = call.get('call_depth'), call.get('max_call_depth')
    if type(depth) is not int or type(maximum) is not int or not 1 <= depth <= maximum <= 5:
        raise ValueError('invalid or exceeded call depth')
    ttl = call.get('ttl_seconds')
    if type(ttl) is not int or not 1 <= ttl <= 86400:
        raise ValueError('invalid TTL')
    created = parse_iso(call['created_at'])
    if created.tzinfo is None:
        raise ValueError('created_at requires timezone')
    current = now or datetime.now(timezone.utc)
    expires = created + timedelta(seconds=ttl)
    if created > current or expires <= current:
        raise ValueError('future or expired call')
    payload = call.get('payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('instruction'), str) or not payload['instruction'].strip():
        raise ValueError('instruction required')
    event = 'EVT-FACP-' + identifier.hex.upper()
    task = 'FACP-' + identifier.hex
    instruction = (f"[AEP:{task}] To {binding['target']} workspace {binding['workspace_id']}\n"
                   f"{payload['instruction']}\nAEP-ACK:{event}")
    envelope = build(order_id=task, event_id=event, workspace_id=binding['workspace_id'],
                     sender=binding['sender'], target=binding['target'], task=task,
                     instruction=instruction, priority='normal',
                     created_at=created.isoformat(), expires_at=expires.isoformat(),
                     session_id=binding['session_id'], session_mode='anchor-required')
    return {'dispatch': envelope,
            'facp': {key: call[key] for key in ('msg_id', 'trace_id', 'fsid', 'caller_node_id', 'target_node_id', 'target_domain')},
            'payload': payload, 'execution_started': False}


def reply(mapped: dict, *, verified: bool, evidence: list[str], summary: str) -> dict:
    if verified and not evidence:
        raise ValueError('verified completion requires evidence')
    source = mapped['facp']
    return {'msg_type': 'COLLAB_REPLY', 'msg_id': str(uuid4()),
            'reply_to': source['msg_id'], 'trace_id': source['trace_id'], 'fsid': source['fsid'],
            'responder_node_id': source['target_node_id'],
            'responder_domain': source['target_domain'], 'elapsed_ms': 0,
            'status': 'success' if verified else 'partial',
            'payload': {'summary': summary, 'artifact_refs': evidence},
            'error': None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--call', type=Path, required=True)
    parser.add_argument('--binding', type=Path, required=True)
    args = parser.parse_args()
    result = translate(json.loads(args.call.read_text(encoding='utf-8')),
                       json.loads(args.binding.read_text(encoding='utf-8')))
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
