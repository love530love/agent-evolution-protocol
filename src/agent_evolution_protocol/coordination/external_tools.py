"""Optional upstream CLI boundaries; probing never grants delivery status."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


UPSTREAMS = {
    'agent_desktop': {'repository': 'https://github.com/vercel-labs/agent-desktop',
                      'command': 'agent-desktop', 'probe': ['status'],
                      'mcp_args': ['mcp'], 'role': 'desktop executor'},
    't3code': {'repository': 'https://github.com/pingdotgg/t3code',
               'command': 't3', 'probe': ['--help'],
               'role': 'supported agent harness control'},
    'lobehub': {'repository': 'https://github.com/lobehub/lobehub',
                'command': 'lh', 'probe': ['--help'],
                'role': 'external collaboration frontend'},
}


def resolve(provider: str) -> list[str]:
    repo = Path(__file__).resolve().parents[3]
    base = repo / '.tools' / 'external-agent-tools' / 'node_modules'
    if provider == 't3code':
        binary = repo / '.tools/t3-native/0.1.7/t3.exe'
        if binary.is_file():
            return [str(binary)]
    if provider == 'agent_desktop':
        binary = base / 'agent-desktop/bin/agent-desktop-win32-x64.exe'
        if binary.is_file():
            return [str(binary)]
    entry = {'t3code': 't3/bin/t3.mjs', 'lobehub': '@lobehub/cli/dist/index.js'}.get(provider)
    node = shutil.which('node.exe') or shutil.which('node')
    if entry and node and (base / entry).is_file():
        return [node, str(base / entry)]
    executable = shutil.which(UPSTREAMS[provider]['command'])
    return [executable] if executable else []


def plan(provider: str) -> dict:
    spec = UPSTREAMS[provider]
    prefix = resolve(provider)
    executable = prefix[0] if prefix else None
    return {'provider': provider, **spec, 'executable': executable,
            'argv': [*prefix, *spec['probe']] if executable else [],
            'status': 'AVAILABLE_UNPROBED' if executable else 'NOT_CONFIGURED',
            'delivery_verified': False, 'execution_started': False}


def probe(provider: str, *, timeout: float = 15) -> dict:
    result = plan(provider)
    if not result['argv']:
        return result
    try:
        completed = subprocess.run(result['argv'], capture_output=True,
                                   encoding='utf-8', errors='replace', timeout=timeout,
                                   shell=False)
        result.update(status='PROBED' if completed.returncode == 0 else 'FAILED',
                      exit_code=completed.returncode, stdout=completed.stdout[:16000],
                      stderr=completed.stderr[:4000], execution_started=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.update(status='PROBE_ERROR', error=str(exc), execution_started=True)
    return result


def message_plan(provider: str, session_id: str, text: str) -> dict:
    """Build an existing-session message; caller supplies text through stdin."""
    if not session_id or session_id.startswith('-') or not text.strip():
        raise ValueError('existing session id and nonempty text required')
    prefix = resolve(provider)
    if not prefix:
        raise ValueError('provider not installed')
    if provider == 't3code':
        args = ['thread', 'send', session_id, '-', '--wait', '--json']
        data = Path(__file__).resolve().parents[3] / '.tools/t3-data'
        if data.is_dir():
            args.extend(['--base-dir', str(data)])
        stdin = text
    elif provider == 'lobehub':
        raise ValueError('LobeHub server integration disabled: local-only project policy')
    else:
        raise ValueError('desktop messages require a bound UI workflow')
    return {'provider': provider, 'session_id': session_id,
            'argv': [*prefix, *args], 'stdin': stdin,
            'execution_started': False, 'delivery_verified': False}


def local_worker_plan(agent_type: str, workspace: Path, session_id: str) -> dict:
    """Resume an explicitly bound native CLI session through LobeHub, offline."""
    if agent_type not in {'codex', 'qoder', 'codebuddy', 'claude-code', 'copilot'}:
        raise ValueError('unsupported worker type')
    if not workspace.is_dir() or not session_id or session_id.startswith('-'):
        raise ValueError('existing workspace and native session id required')
    prefix = resolve('lobehub')
    if not prefix:
        raise ValueError('LobeHub CLI not installed')
    return {'argv': [*prefix, 'hetero', 'exec', '--type', agent_type,
                     '--cwd', str(workspace.resolve()), '--resume', session_id,
                     '--prompt', '-'], 'stdin_required': True,
            'server_ingest': False, 'execution_started': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'plan', 'probe', 'message-plan'])
    parser.add_argument('--provider', choices=UPSTREAMS)
    parser.add_argument('--session-id')
    parser.add_argument('--text-file', type=Path)
    args = parser.parse_args()
    if args.action != 'inventory' and not args.provider:
        parser.error('--provider required')
    if args.action == 'message-plan':
        if not args.session_id or not args.text_file:
            parser.error('--session-id and --text-file required')
        result = message_plan(args.provider, args.session_id, args.text_file.read_text(encoding='utf-8'))
    else:
        result = ([plan(name) for name in UPSTREAMS] if args.action == 'inventory'
              else probe(args.provider) if args.action == 'probe' else plan(args.provider))
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if args.action != 'probe' or result['status'] == 'PROBED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
