from agent_evolution_protocol.coordination.process_probe import probe

def test_invalid_pid_rejected():
    try: probe(0)
    except ValueError: pass
    else: raise AssertionError("invalid pid accepted")
