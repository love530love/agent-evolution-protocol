from unittest.mock import patch
import subprocess

from agent_evolution_protocol.coordination.external_tools import plan, probe
from agent_evolution_protocol.coordination.external_tools import message_plan
import pytest
from agent_evolution_protocol.coordination.external_tools import local_worker_plan


def test_missing_cli_does_not_execute():
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=[]), patch('subprocess.run') as run:
        assert probe('agent_desktop')['status'] == 'NOT_CONFIGURED'
        run.assert_not_called()


def test_probe_success_is_not_delivery():
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=['C:/tools/agent-desktop.exe']), patch(
        'subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{}', '')
    ) as run:
        result = probe('agent_desktop')
        assert result['status'] == 'PROBED'
        assert result['delivery_verified'] is False
        assert run.call_args.kwargs['shell'] is False
        assert run.call_args.args[0] == ['C:/tools/agent-desktop.exe', 'status']


def test_lobehub_uses_verified_upstream_cli():
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=['node.exe', 'lh.js']):
        assert plan('lobehub')['argv'] == ['node.exe', 'lh.js', '--help']


def test_probe_timeout_retains_unknown_delivery():
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=['t3.exe']), patch(
        'subprocess.run', side_effect=subprocess.TimeoutExpired('t3', 15)
    ):
        result = probe('t3code')
        assert result['status'] == 'PROBE_ERROR'
        assert result['delivery_verified'] is False


def test_t3_resume_message_keeps_text_off_argv():
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=['t3.exe']):
        result = message_plan('t3code', 'thread-1', 'hello 世界')
        assert result['argv'][:7] == ['t3.exe', 'thread', 'send', 'thread-1', '-', '--wait', '--json']
        assert result['stdin'] == 'hello 世界'
        assert result['execution_started'] is False


def test_lobehub_real_notify_contract():
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=['node.exe', 'lh.js']):
        with pytest.raises(ValueError, match='local-only'):
            message_plan('lobehub', 'topic-1', 'a; b')


def test_reject_option_as_session():
    with pytest.raises(ValueError):
        message_plan('t3code', '--new', 'hello')


def test_local_worker_never_uses_server_ingest(tmp_path):
    with patch('agent_evolution_protocol.coordination.external_tools.resolve', return_value=['node', 'lh.js']):
        result = local_worker_plan('qoder', tmp_path, 'native-1')
        assert '--topic' not in result['argv']
        assert '--resume' in result['argv']
        assert result['server_ingest'] is False
