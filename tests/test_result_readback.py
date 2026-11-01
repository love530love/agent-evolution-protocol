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


class SessionGuardTests(unittest.TestCase):
    """Which conversation a line came from decides whether it may be compared.

    The transcript is read per agent, so it carries every window that agent ever
    spoke in: an earlier task, another window, another task's leftovers.  Before
    the guard was wired the whole transcript was compared, and a line from
    somewhere else could confirm -- or contradict -- a claim it never made.
    """

    def test_only_the_anchored_conversation_is_compared(self):
        # The claim's path is in this conversation, a different path is in
        # another window of the same agent.  The other window must not turn
        # into "different_artifact": it was working on something else.
        observed = result_readback.observe(
            [_event(f"wrote {REPORT}", event_id="evt-1"),
             _event("wrote K:/proj/out/notes.txt", session_id="9999", event_id="evt-2")],
            session_id=SESSION)
        self.assertTrue(observed["session_checked"])
        self.assertFalse(observed["session_unknown"])
        self.assertEqual(observed["session_reason"], "applied")
        self.assertEqual(observed["artifacts"], [REPORT])
        value = result_readback.judge(result_readback.claim(_state()), observed)
        self.assertIs(value["match"], True)
        self.assertEqual(value["evidence"]["rejected"][0]["reason"], "foreign_session")

    def test_a_path_from_another_conversation_is_not_comparable(self):
        """A path from another conversation is missing attribution, not a refutation.

        This used to be ``False``/``foreign_session``: the only result-bearing
        line came from elsewhere, so the claim was called a contradiction.  That
        reads the fact backwards -- the product exists, what we lack is proof
        that *this* task's conversation produced it -- and blocking on it stalls
        a task whose work may be done.
        """
        value = _judge(_state(), [_event(f"wrote {REPORT}", session_id="9999")])
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "foreign_session")
        self.assertFalse(result_readback.may_complete(value))
        self.assertTrue(value["evidence"]["session_checked"])
        self.assertEqual(value["evidence"]["rejected"][0]["reason"], "foreign_session")

    def test_the_two_ways_of_having_nothing_are_named_differently(self):
        # Same verdict, different gap, and the caller must not have to read
        # ``rejected`` to tell them apart: an empty capture has nothing to
        # re-read, a foreign line points at a conversation that does.
        silent = _judge(_state(), [])
        elsewhere = _judge(_state(), [_event(f"wrote {REPORT}", session_id="9999")])
        self.assertIsNone(silent["match"])
        self.assertIsNone(elsewhere["match"])
        self.assertEqual(silent["reason"], "no_result_in_transcript")
        self.assertEqual(elsewhere["reason"], "foreign_session")
        self.assertNotEqual(silent["reason"], elsewhere["reason"])

    def test_a_line_without_a_conversation_cannot_verify_anything(self):
        """A line with no session id proves nothing about this conversation.

        The first version of this module compared it anyway, on the grounds that
        a capture gap is not evidence of a foreign conversation.  True -- but it
        is not evidence of this one either, so the path being exactly the claimed
        one changes nothing.  Treating "we do not know whose line this is" as
        "it is ours" is how a readback becomes a decoration.
        """
        observed = result_readback.observe([_event(f"wrote {REPORT}", session_id="")],
                                           session_id=SESSION)
        self.assertFalse(observed["session_checked"])
        self.assertTrue(observed["session_unknown"])
        self.assertEqual(observed["session_reason"], "session_unknown")
        self.assertEqual(observed["artifacts"], [])
        value = result_readback.judge(result_readback.claim(_state()), observed)
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "session_unknown")
        self.assertTrue(value["evidence"]["session_unknown"])
        self.assertFalse(result_readback.may_complete(value))

    def test_a_line_missing_the_key_entirely_is_unknown_too(self):
        # The same fact in the other shape a capture can produce: the key is
        # absent rather than empty.  Both mean "we cannot attribute this".
        event = _event(f"wrote {REPORT}")
        event.pop("session_id")
        observed = result_readback.observe([event], session_id=SESSION)
        self.assertTrue(observed["session_unknown"])
        self.assertIsNone(result_readback.judge(result_readback.claim(_state()),
                                               observed)["match"])

    def test_a_partly_guarded_transcript_still_verifies_and_says_it_is_partial(self):
        # One anchored line names the artifact, one line cannot be attributed.
        # The match rests on the anchored line, so it stands, and the receipt
        # records that the guard was only partial -- and that the unattributable
        # line's path was not used to disagree.
        observed = result_readback.observe(
            [_event(f"wrote {REPORT}", event_id="evt-1"),
             _event("wrote K:/proj/out/notes.txt", session_id="", event_id="evt-2")],
            session_id=SESSION)
        self.assertTrue(observed["session_checked"])
        self.assertTrue(observed["session_unknown"])
        self.assertEqual(observed["session_reason"], "partial")
        value = result_readback.judge(result_readback.claim(_state()), observed)
        self.assertIs(value["match"], True)

    def test_no_line_reaching_the_guard_is_not_reported_as_applied(self):
        # The only line is our own outgoing order, so the conversation guard
        # never saw anything.  ``session_checked`` already says False; the
        # reason says it too, so a receipt cannot be read as "guard applied".
        observed = result_readback.observe(
            [_event(f"please write {REPORT}", direction="outgoing")], session_id=SESSION)
        self.assertFalse(observed["session_checked"])
        self.assertFalse(observed["session_unknown"])
        self.assertEqual(observed["session_reason"], "not_compared")

    def test_without_a_session_id_the_whole_transcript_is_compared_and_says_so(self):
        # What this module did before the guard existed.  Kept because a caller
        # holding no conversation id should still get a readback -- but the
        # observation must not look guarded, so ``session_checked`` is False and
        # the reason says the guard was never asked for.
        observed = result_readback.observe([_event(f"wrote {REPORT}")])
        self.assertFalse(observed["session_checked"])
        self.assertFalse(observed["session_unknown"])
        self.assertEqual(observed["session_reason"], "not_requested")
        value = result_readback.judge(result_readback.claim(_state()), observed)
        self.assertIs(value["match"], True)
        self.assertEqual(value["evidence"]["session_reason"], "not_requested")

    def test_a_refused_line_still_names_the_reason_when_nothing_else_matched(self):
        # Found by wiring the guard into the supervisor, not by this module's own
        # tests: an ACK line in the anchored conversation makes the transcript
        # "attributable", so the earlier guard does not fire -- but it carries no
        # artifact, so the verdict fell through to "no_result_in_transcript" and
        # dropped the fact that the output *was* found, in another conversation.
        # Both are None, and they are not the same news: one says go read the
        # other window, the other says there is nothing to read.
        observed = result_readback.observe(
            [_event("AEP-ACK:evt-1", event_id="evt-1"),
             _event(f"wrote {REPORT}", session_id="9999", event_id="evt-2")],
            session_id=SESSION)
        self.assertTrue(observed["session_checked"])
        self.assertEqual(observed["artifacts"], [])
        value = result_readback.judge(result_readback.claim(_state()), observed)
        self.assertIsNone(value["match"])
        self.assertEqual(value["reason"], "foreign_session")


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
