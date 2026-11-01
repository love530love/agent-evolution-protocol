"""A completion is a claim until the transcript shows the same artifact.

``assignment.complete_evidence()`` accepts the agent writing ``phase=done``
about itself, so today "the work happened" is the agent's own word.  Every test
below is one way that word can be wrong, and the point of most of them is that
the module answers ``None`` instead of ``True`` when it cannot tell.
"""

from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.coordination import result_readback


REPORT = "K:/proj/out/report.md"
SESSION = "4242"


def _state(**extra):
    payload = {"task": "T1", "owner": "qoder", "phase": "done",
               "goal": "write the report", "evidence": [REPORT],
               "blocked_reason": "", "next_action": ""}
    payload.update(extra)
    return payload


def _event(text, *, role="assistant", direction="incoming", session_id=SESSION,
           event_id="evt-1"):
    return {"id": event_id, "role": role, "direction": direction,
            "session_id": session_id, "recorded_at": "2026-11-01T12:00:00+00:00",
            "added_lines": [text]}


def _judge(state, events, **extra):
    return result_readback.judge(result_readback.claim(state),
                                 result_readback.observe(events, session_id=SESSION), **extra)


class VerifiedResultTests(unittest.TestCase):
    def test_an_artifact_the_transcript_also_names_is_verified(self):
        value = _judge(_state(), [_event(f"written to {REPORT}, 42 lines")])
        self.assertIs(value["match"], True)
        self.assertEqual(value["reason"], "")
        self.assertEqual(value["seen"], [REPORT])
        self.assertTrue(result_readback.may_complete(value))

    def test_a_path_written_with_the_other_separator_still_matches(self):
        # The agent types the path it used; the transcript shows the same file
        # in whatever spelling the capture produced.  Comparing the strings
        # rather than the path would fail on every backslash.
        value = _judge(_state(evidence=["K:\\proj\\out\\report.md"]),
                       [_event("done: K:/proj/out/report.md")])
        self.assertIs(value["match"], True)

    def test_a_relative_suffix_of_the_claimed_path_is_the_same_file(self):
        value = _judge(_state(), [_event("wrote out/report.md")])
        self.assertIs(value["match"], True)

    def test_the_completed_phase_spelling_is_also_a_done_claim(self):
        value = _judge(_state(phase="completed"), [_event(f"see {REPORT}")])
        self.assertTrue(result_readback.may_complete(value))


class NothingToCompareTests(unittest.TestCase):
    def test_an_empty_transcript_is_unknown_and_never_a_match(self):
        # A capture that failed, a client that was closed, and an agent that
        # produced nothing all look like this.  Reading it as agreement is how
        # the readback becomes decoration, so the answer is None -- not True,
        # and not False either, because no one has looked at anything.
        value = _judge(_state(), [])
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "no_result_in_transcript")
        self.assertFalse(result_readback.may_complete(value))

    def test_our_own_outgoing_line_cannot_verify_the_agents_work(self):
        # The assignment text names the path it expects; a detector that scans
        # for the string finds it in our own message and reports success.
        value = _judge(_state(), [_event(f"please write {REPORT}",
                                        role="assistant", direction="outgoing")])
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "no_result_in_transcript")
        self.assertEqual(value["evidence"]["rejected"][0]["reason"], "not_incoming")

    def test_a_claim_that_names_no_artifact_is_unknown_not_verified(self):
        # "done" and "tests pass" are words.  There is nothing a second party
        # can open, so the honest answer is that nothing was checked.
        value = _judge(_state(evidence=["done", "tests pass"]), [_event("all done")])
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "claim_without_artifact")
        self.assertEqual(value["evidence"]["unchecked"], ["done", "tests pass"])
        self.assertFalse(result_readback.may_complete(value))

    def test_a_working_write_back_is_not_a_completion(self):
        # A ``working`` phase carrying paths is progress.  Verifying those paths
        # would let the next heartbeat close a task nobody finished.
        value = _judge(_state(phase="working"), [_event(f"started {REPORT}")])
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "claim_not_done")
        self.assertFalse(value["claim_done"])
        self.assertFalse(result_readback.may_complete(value))

    def test_partial_evidence_is_not_a_match(self):
        # One of two claimed files appears.  What is named checks out; what is
        # not named is unknown, and unknown is not done.
        value = _judge(_state(evidence=[REPORT, "K:/proj/out/chart.png"]),
                       [_event(f"wrote {REPORT}")])
        self.assertIsNone(value["match"])
        self.assertEqual(value["seen"], [REPORT])


class DisagreementTests(unittest.TestCase):
    def test_a_different_artifact_in_the_transcript_is_a_mismatch(self):
        # The agent says it produced report.md and the transcript shows it
        # producing something else.  That is a disagreement, not a gap.
        value = _judge(_state(), [_event("wrote K:/proj/out/notes.txt")])
        self.assertIs(value["match"], False)
        self.assertEqual(value["reason"], "different_artifact")
        self.assertFalse(result_readback.may_complete(value))

    def test_a_same_named_file_in_another_directory_is_not_the_artifact(self):
        # Basename equality is not file equality; accepting it is how a stale
        # copy in the wrong folder completes a task.
        value = _judge(_state(evidence=["K:/proj/old/report.md"]),
                       [_event("wrote docs/report.md")])
        self.assertIs(value["match"], False)
        self.assertEqual(value["reason"], "different_artifact")

    def test_a_reply_from_another_conversation_is_not_this_tasks_result(self):
        # Another session of the same agent answering the same question is not
        # this task's answer -- the same guard the ACK detector needs.
        value = _judge(_state(), [_event(f"wrote {REPORT}", session_id="9999")])
        self.assertIs(value["match"], False)
        self.assertEqual(value["reason"], "foreign_session")
        self.assertFalse(result_readback.may_complete(value))

    def test_an_event_without_a_session_is_checked_and_says_so(self):
        # A capture gap is not evidence of a foreign conversation, so the guard
        # does not fire -- but the verdict records that it did not.
        observed = result_readback.observe([_event(f"wrote {REPORT}", session_id="")],
                                           session_id=SESSION)
        self.assertFalse(observed["session_checked"])
        value = result_readback.judge(result_readback.claim(_state()), observed)
        self.assertIs(value["match"], True)


class DiskTests(unittest.TestCase):
    def test_a_claimed_artifact_that_is_not_on_disk_refutes_the_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "out").mkdir()
            Path(folder, "out", "report.md").write_text("x", encoding="utf-8")
            good = _judge(_state(evidence=["out/report.md"]),
                          [_event("wrote out/report.md")], root=folder)
            self.assertIs(good["match"], True)
            missing = _judge(_state(evidence=["out/missing.md"]),
                             [_event("wrote out/missing.md")], root=folder)
            self.assertIs(missing["match"], False)
            self.assertEqual(missing["reason"], "artifact_missing_on_disk")
            self.assertEqual(missing["missing_on_disk"], ["out/missing.md"])
            self.assertFalse(result_readback.may_complete(missing))

    def test_without_a_root_the_module_does_not_guess_about_the_disk(self):
        value = _judge(_state(), [_event(f"wrote {REPORT}")])
        self.assertIs(value["match"], True)
        self.assertEqual(value["missing_on_disk"], [])


class MayCompleteTests(unittest.TestCase):
    def test_may_complete_refuses_anything_that_is_not_a_verified_match(self):
        self.assertFalse(result_readback.may_complete({}))
        self.assertFalse(result_readback.may_complete(None))
        self.assertFalse(result_readback.may_complete({"match": False, "claim_done": True}))
        # The whole point: an unknown must not read as a pass.
        self.assertFalse(result_readback.may_complete({"match": None, "claim_done": True}))
        # A match without a done claim is not a completion either.
        self.assertFalse(result_readback.may_complete({"match": True, "claim_done": False}))
        # And a self-contradicting dict is exactly what a bug produces.
        self.assertFalse(result_readback.may_complete(
            {"match": True, "claim_done": True, "reason": "different_artifact"}))
        self.assertTrue(result_readback.may_complete({"match": True, "claim_done": True}))


class ContractTests(unittest.TestCase):
    def test_the_verdict_does_not_claim_a_second_readback_contract(self):
        # This repository already paid for a parallel contract: four invented
        # federation schemas beside a standard set.  The verdict is a
        # judgement, so it carries no schema name at all, and ``aep-readback-v1``
        # stays the only receipt anyone has to accept.
        value = _judge(_state(), [_event(f"wrote {REPORT}")])
        self.assertNotIn("schema", value)
        self.assertEqual(result_readback.READBACK_SCHEMA, "aep-readback-v1")

    def test_done_phases_agree_with_the_federation_definition(self):
        # If ``assignment`` renames a phase, this module would keep returning
        # ``claim_not_done`` for a write-back everyone else calls finished.
        from agent_evolution_protocol.federation import assignment

        self.assertEqual(result_readback.DONE_PHASES, assignment.DONE_PHASES)

    def test_judge_rejects_a_non_object(self):
        with self.assertRaises(ValueError):
            result_readback.judge([], {})
        with self.assertRaises(ValueError):
            result_readback.claim("done")


if __name__ == "__main__":
    unittest.main()
