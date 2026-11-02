"""The same conversation, seen through a different surface.

A digest answers "is this the identical window". Copilot's cross-surface model
needs a different question -- "is this the same conversation, now shown
somewhere else" -- and the digest cannot answer it, because it covers the exe
and the title too. These tests pin the split that makes the question askable,
and they pin the direction of every uncertain answer: an unknown is ``None``,
never ``True``.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agent_evolution_protocol.coordination import session_fingerprint


MARKER = "赛事夺冠执行"


def row(agent: str = "qoder", *, exe: str = r"D:\Programs\Qoder\Qoder.exe",
        title: str = "Qoder", marker: str = MARKER, model: str = "DeepSeek-Flash"):
    return session_fingerprint.observe(
        agent=agent, window={"pid": 1, "title": title, "exe": exe},
        profile={"session_marker": marker, "model_marker": model})


class ContinuityTests(unittest.TestCase):
    def test_the_same_window_continues(self):
        result = session_fingerprint.continuity(row(), row())
        self.assertTrue(result["continues"])
        self.assertTrue(result["same_surface"])
        self.assertEqual(result["reason"], session_fingerprint.REASON_SAME_SURFACE)

    def test_another_client_showing_the_same_conversation_continues(self):
        moved = row("workbuddy3", exe=r"D:\Programs\WorkBuddy\WorkBuddy.exe",
                    title="WorkBuddy")
        result = session_fingerprint.continuity(row(), moved)
        self.assertTrue(result["continues"])
        self.assertFalse(result["same_surface"])
        self.assertEqual(result["reason"], session_fingerprint.REASON_SURFACE_CHANGED)
        self.assertEqual(result["surface_differs"], ["agent", "exe", "title"])

    def test_the_digest_disagrees_which_is_the_whole_point(self):
        # If these two ever agree, the split is decoration: the digest covers
        # the surface, so a surface move breaks it by construction.
        moved = row("workbuddy3", exe=r"D:\Programs\WorkBuddy\WorkBuddy.exe",
                    title="WorkBuddy")
        self.assertFalse(session_fingerprint.compare(row(), moved)["match"])
        self.assertTrue(session_fingerprint.continuity(row(), moved)["continues"])

    def test_two_named_conversations_do_not_continue(self):
        result = session_fingerprint.continuity(row(), row(marker="另一个任务"))
        self.assertFalse(result["continues"])
        self.assertEqual(result["reason"], session_fingerprint.REASON_DIFFERENT_SESSION)

    def test_an_unlabelled_conversation_is_unknown_not_continuous(self):
        # Two threads in the same client with no marker are indistinguishable.
        # Answering True here is how a message lands in the wrong one.
        result = session_fingerprint.continuity(row(marker=""), row(marker=""))
        self.assertIsNone(result["continues"])
        self.assertEqual(result["reason"], session_fingerprint.REASON_WEAK_SESSION_MARKER)

    def test_a_short_marker_is_not_an_identity(self):
        result = session_fingerprint.continuity(row(marker="新会"), row(marker="新会"))
        self.assertIsNone(result["continues"])

    def test_nothing_recorded_is_unknown(self):
        result = session_fingerprint.continuity(None, row())
        self.assertIsNone(result["continues"])
        self.assertEqual(result["reason"], session_fingerprint.REASON_NO_RECORDED_SURFACE)

    def test_switching_model_does_not_start_a_new_conversation(self):
        result = session_fingerprint.continuity(row(), row(model="GPT-6"))
        self.assertTrue(result["continues"])
        self.assertTrue(result["model_changed"])

    def test_the_marker_is_normalised_like_the_digest_normalises_it(self):
        result = session_fingerprint.continuity(row(), row(marker=f"  {MARKER}  "))
        self.assertTrue(result["continues"])

    def test_an_observation_must_be_an_object(self):
        with self.assertRaises(ValueError):
            session_fingerprint.continuity(row(), "not a row")


class GroupingTests(unittest.TestCase):
    def test_one_conversation_on_two_clients_is_one_group(self):
        groups = session_fingerprint.group_by_conversation([
            row(), row("workbuddy3", exe=r"D:\Programs\WorkBuddy\WorkBuddy.exe",
                       title="WorkBuddy")])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["surfaces"]), 2)
        self.assertTrue(groups[0]["identified"])

    def test_two_conversations_are_two_groups(self):
        groups = session_fingerprint.group_by_conversation([
            row(), row(marker="修复或对齐昆仑容器的triton包")])
        self.assertEqual(len(groups), 2)

    def test_unlabelled_rows_are_not_merged_into_one_group(self):
        # Merging them would produce a group claiming to be every unlabelled
        # conversation at once, which reads like a surface count that means
        # nothing.
        groups = session_fingerprint.group_by_conversation([
            row(marker=""), row(agent="wb", marker="")])
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertFalse(group["identified"])
            self.assertEqual(group["conversation"], "")

    def test_junk_rows_are_skipped_rather_than_fatal(self):
        groups = session_fingerprint.group_by_conversation([None, "x", row()])  # type: ignore[list-item]
        self.assertEqual(len(groups), 1)


class RecordedSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_all_recorded_fills_in_the_agent_from_the_filename(self):
        session_fingerprint.record(self.root, "qoder", row())
        folder = session_fingerprint.directory(self.root)
        target = folder / "orphan.json"
        target.write_text(json.dumps({"session_marker": MARKER}, ensure_ascii=False),
                          encoding="utf-8")
        agents = {item["agent"] for item in session_fingerprint.all_recorded(self.root)}
        self.assertEqual(agents, {"qoder", "orphan"})

    def test_an_unreadable_file_is_skipped(self):
        folder = session_fingerprint.directory(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "broken.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(session_fingerprint.all_recorded(self.root), [])

    def test_a_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(session_fingerprint.all_recorded(self.root / "absent"), [])

    def test_two_recorded_surfaces_are_found_offline(self):
        session_fingerprint.record(self.root, "qoder", row())
        session_fingerprint.record(self.root, "workbuddy3", row(
            "workbuddy3", exe=r"D:\Programs\WorkBuddy\WorkBuddy.exe", title="WorkBuddy"))
        groups = session_fingerprint.group_by_conversation(
            session_fingerprint.all_recorded(self.root))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["surfaces"]), 2)


if __name__ == "__main__":
    unittest.main()
