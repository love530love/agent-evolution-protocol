import json
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest
from agent_evolution_protocol.coordination.external_runner import execute


def run(root, workspace, text='你好'):
    return execute(root, event_id='EVT-test-001', workspace=workspace,
                   plan={'argv': ['fake.exe']}, text=text)


def test_same_event_runs_once(tmp_path):
    with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'reply', '')) as child:
        first = run(tmp_path/'receipts', tmp_path)
        assert run(tmp_path/'receipts', tmp_path) == first
        child.assert_called_once()
        assert child.call_args.kwargs['input'] == '你好'
        assert first['delivery_verified'] is False


def test_changed_request_refused(tmp_path):
    with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
        run(tmp_path/'receipts', tmp_path)
        with pytest.raises(ValueError, match='different request'):
            run(tmp_path/'receipts', tmp_path, 'changed')


def test_timeout_never_replayed(tmp_path):
    with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('fake', 1)) as child:
        assert run(tmp_path/'receipts', tmp_path)['state'] == 'UNKNOWN'
        assert run(tmp_path/'receipts', tmp_path)['state'] == 'UNKNOWN'
        child.assert_called_once()


def test_crash_after_reservation_not_replayed(tmp_path):
    with patch('subprocess.run', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run(tmp_path/'receipts', tmp_path)
    with patch('subprocess.run') as child:
        assert run(tmp_path/'receipts', tmp_path)['state'] == 'UNKNOWN'
        child.assert_not_called()
