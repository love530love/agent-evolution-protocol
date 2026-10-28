from agent_evolution_protocol.coordination.event_watch import available

def test_event_watch_is_optional():
    assert isinstance(available(), bool)
