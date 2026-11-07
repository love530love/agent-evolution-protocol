from datetime import datetime, timezone
from uuid import uuid4
import pytest
from agent_evolution_protocol.coordination.facp import translate, reply
from agent_evolution_protocol.coordination.dispatch_contract import validate


def fixture():
    call = dict(msg_type='COLLAB_CALL', msg_id=str(uuid4()), trace_id='tr-1', fsid='fs-a-001',
                caller_node_id='node-a', target_node_id='node-b', caller_domain='local',
                target_domain='local', intent='code_review', call_depth=1, max_call_depth=5,
                cross_domain=False, ttl_seconds=300, created_at='2026-11-07T10:00:00+00:00',
                payload={'instruction':'Review the kernel', 'artifact_refs':['kernel.py']})
    binding = dict(fsid='fs-a-001', caller_node_id='node-a', target_node_id='node-b',
                   caller_domain='local', target_domain='local', workspace_id='ws-a',
                   session_id='native-1', sender='codex', target='qoder')
    return call, binding


def convert(c,b):
    return translate(c,b,now=datetime(2026,11,7,10,1,tzinfo=timezone.utc))


def test_roundtrip():
    c,b=fixture(); m=convert(c,b)
    validate(m['dispatch'], strict=True)
    assert m['dispatch']['session_policy']['session_id']=='native-1'
    assert not m['dispatch']['session_policy']['allow_new_session']
    r=reply(m,verified=True,evidence=['result.json'],summary='done')
    assert r['reply_to']==c['msg_id'] and r['trace_id']==c['trace_id']


@pytest.mark.parametrize('field,value', [('fsid','wrong'),('target_node_id','wrong'),
    ('caller_node_id','fake'),('call_depth',True),('call_depth',6),('ttl_seconds',0),
    ('created_at','2026-11-06T10:00:00+00:00'),('intent','desktop_execute')])
def test_invalid_call(field,value):
    c,b=fixture(); c[field]=value
    with pytest.raises(ValueError): convert(c,b)


def test_missing_session():
    c,b=fixture(); del b['session_id']
    with pytest.raises(ValueError): convert(c,b)


def test_cross_domain_not_implied_by_token():
    c,b=fixture(); c.update(target_domain='intl',cross_domain=True,audit_token='fake')
    b['target_domain']='intl'
    with pytest.raises(ValueError,match='verifier'): convert(c,b)


def test_no_success_without_evidence():
    c,b=fixture()
    with pytest.raises(ValueError): reply(convert(c,b),verified=True,evidence=[],summary='done')
