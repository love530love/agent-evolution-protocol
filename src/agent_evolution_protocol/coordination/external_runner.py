"""Durable local CLI execution with an immutable attempt fence."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import math
from uuid import uuid4

from .external_tools import local_worker_plan, message_plan, resolve


def execute(root: Path, *, event_id: str, workspace: Path, plan: dict,
            text: str = '', timeout: float = 120) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', event_id):
        raise ValueError('invalid event id')
    if not workspace.is_dir() or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('existing workspace and positive timeout required')
    argv = plan.get('argv')
    if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or '\0' in v for v in argv) or not argv[0]:
        raise ValueError('argv must be a nonempty string array')
    root.mkdir(parents=True, exist_ok=True)
    request = {'argv': plan['argv'], 'stdin': text, 'workspace': str(workspace.resolve())}
    digest = sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    attempt = root / (event_id + '.attempt.json')
    result_path = root / (event_id + '.result.json')
    try:
        # Reserve before spawning. A crash leaves a durable UNKNOWN, never a replay.
        with attempt.open('x', encoding='utf-8') as handle:
            json.dump({'request_sha256': digest, 'event_id': event_id,
                       'workspace': request['workspace'], 'state': 'STARTING'}, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        try:
            existing = json.loads(attempt.read_text(encoding='utf-8'))
            if not isinstance(existing, dict) or 'request_sha256' not in existing:
                raise ValueError('incomplete attempt')
        except (OSError, ValueError):
            return {'event_id': event_id, 'state': 'UNKNOWN', 'replay_allowed': False,
                    'error': 'attempt incomplete or unreadable; reconcile before retry'}
        if existing['request_sha256'] != digest:
            raise ValueError('event id already reserved for a different request')
        if result_path.exists():
            try:
                saved = json.loads(result_path.read_text(encoding='utf-8'))
                if saved.get('request_sha256') == digest and saved.get('event_id') == event_id:
                    return saved
            except (OSError, ValueError, AttributeError):
                pass
        return {'event_id': event_id, 'state': 'UNKNOWN', 'replay_allowed': False}
    result = {'event_id': event_id, 'workspace': request['workspace'],
              'request_sha256': digest, 'delivery_verified': False,
              'replay_allowed': False}
    try:
        done = subprocess.run(plan['argv'], input=text, cwd=workspace,
                              capture_output=True, encoding='utf-8', errors='replace',
                              timeout=timeout, shell=False)
        result.update(state='PROCESS_EXITED' if done.returncode == 0 else 'PROCESS_FAILED',
                      exit_code=done.returncode, stdout=done.stdout, stderr=done.stderr)
    except subprocess.TimeoutExpired:
        result.update(state='UNKNOWN', error='timeout; inspect native session before any new attempt')
    except OSError as exc:
        result.update(state='NOT_STARTED', error=str(exc))
    temporary = result_path.with_name(result_path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['desktop-read', 't3-send', 'worker-resume'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--event-id', required=True)
    parser.add_argument('--session-id')
    parser.add_argument('--agent-type')
    parser.add_argument('--text-file', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    text = args.text_file.read_text(encoding='utf-8') if args.text_file else ''
    if args.action == 'desktop-read':
        prefix = resolve('agent_desktop')
        if not prefix:
            parser.error('agent-desktop not installed')
        plan = {'argv': [*prefix, 'list-apps']}
    elif args.action == 't3-send':
        plan = message_plan('t3code', args.session_id, text)
    else:
        plan = local_worker_plan(args.agent_type, args.workspace, args.session_id)
        if not text.strip():
            parser.error('--text-file must contain a task')
    result = execute(args.root, event_id=args.event_id, workspace=args.workspace,
                     plan=plan, text=text, timeout=args.timeout) if args.apply else plan
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if not args.apply or result['state'] == 'PROCESS_EXITED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
