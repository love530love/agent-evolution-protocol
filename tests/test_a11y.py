"""The screen-reader flag is a machine-wide switch; test it without flipping it.

Toggling SPI_SETSCREENREADER in a test would change how every application on
the developer's desktop behaves, so these tests only read state and verify the
command is wired.  The one write is a no-op round-trip: set the flag to the
value it already has.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import a11y, session_lease, ui_fallback


class PlatformTests(unittest.TestCase):
    def test_availability_matches_the_platform(self):
        self.assertEqual(a11y.available(), sys.platform == "win32")

    def test_calls_fail_loudly_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("this assertion is about the non-Windows path")
        self.assertFalse(a11y.available())
        with self.assertRaises(RuntimeError):
            a11y.status()
        # Elevation is reported rather than raised: callers treat "unknown" as
        # "cannot drive this window", which is the honest answer.
        self.assertEqual(a11y.elevation(os.getpid()), "unknown")
        self.assertEqual(a11y.check()["available"], False)


@unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
class WindowsTests(unittest.TestCase):
    def test_status_is_a_bool(self):
        self.assertIn(a11y.status(), (True, False))

    def test_self_elevation_is_reported(self):
        self.assertIn(a11y.elevation(os.getpid()), ("standard", "elevated"))

    def test_setting_the_current_value_is_a_no_op_round_trip(self):
        before = a11y.status()
        self.assertEqual(a11y.set_enabled(before), before)

    def test_check_reports_what_is_needed_to_drive_a_window(self):
        report = a11y.check(os.getpid())
        self.assertTrue(report["available"])
        self.assertIn("screen_reader", report)
        self.assertEqual(report["target_elevation"], a11y.elevation(os.getpid()))
        # A process can always be driven by itself, so this must be true.
        self.assertTrue(report["drivable"])

    def test_an_unopenable_process_is_not_drivable(self):
        # Pid 0 cannot be opened, which stands in for the elevated-client case:
        # the answer must be "unknown", never a silent yes.
        self.assertEqual(a11y.elevation(0), "unknown")
        self.assertFalse(a11y.check(0)["drivable"])


class AnnotationTests(unittest.TestCase):
    """The driver must say *why* a window cannot be driven, not just fail."""

    def test_reports_are_left_alone_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("this assertion is about the non-Windows path")
        report = {"pid": 4}
        self.assertEqual(ui_fallback.annotate_elevation(report), {"pid": 4})

    def test_a_report_without_a_pid_is_untouched(self):
        report = {"status": "NO_WINDOW"}
        self.assertEqual(ui_fallback.annotate_elevation(report), {"status": "NO_WINDOW"})

    @unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
    def test_an_unreadable_process_is_marked_undrivable(self):
        # Pid 4 is System: opening its token always fails, which is the same
        # answer an elevated client gives.
        report = ui_fallback.annotate_elevation({"pid": 4})
        self.assertEqual(report["target_elevation"], "unknown")
        self.assertFalse(report["drivable"])
        self.assertEqual(report["blocked_by"], "TARGET_NEEDS_ELEVATION")
        self.assertIn("elevated", report["hint"])

    @unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
    def test_our_own_process_is_drivable(self):
        report = ui_fallback.annotate_elevation({"pid": os.getpid()})
        self.assertTrue(report["drivable"])
        self.assertNotIn("blocked_by", report)

    @unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
    def test_both_sides_are_reported(self):
        # A bare "drivable: false" invites the wrong fix; the caller needs to
        # see which side is the high one.
        report = ui_fallback.annotate_elevation({"pid": os.getpid()})
        self.assertIn(report["target_elevation"], ("standard", "elevated"))
        self.assertIn(report["executor_elevation"], ("standard", "elevated"))

    def test_the_comparison_is_what_decides(self):
        # No Windows needed: replace the two probes with a standard executor
        # facing an elevated target and assert the report refuses.
        with mock.patch.object(a11y, "available", return_value=True), \
                mock.patch.object(a11y, "elevation", return_value="elevated"), \
                mock.patch.object(a11y, "self_elevation", return_value="standard"):
            report = ui_fallback.annotate_elevation({"pid": 4})
        self.assertFalse(report["drivable"])
        self.assertEqual(report["blocked_by"], "TARGET_NEEDS_ELEVATION")
        self.assertIn("standard", report["hint"])

    def test_a_standard_target_is_drivable_from_a_standard_executor(self):
        with mock.patch.object(a11y, "available", return_value=True), \
                mock.patch.object(a11y, "elevation", return_value="standard"), \
                mock.patch.object(a11y, "self_elevation", return_value="standard"):
            report = ui_fallback.annotate_elevation({"pid": 4})
        self.assertTrue(report["drivable"])
        self.assertNotIn("blocked_by", report)


class DrivabilityTests(unittest.TestCase):
    """UIPI is one-way, so the answer depends on both sides, not just the target.

    The defect this pins: ``drivable`` used to be "the target's token could be
    read".  That reads correctly only because Windows refuses the token of a
    higher-integrity process -- i.e. the comparison was being performed by the
    failure mode instead of by the code.  Any case where the token *is* readable
    and the executor is still the lower side would have been reported as a
    green light.
    """

    def test_equal_levels_are_drivable(self):
        for level in ("standard", "elevated"):
            self.assertEqual(a11y.drivability(level, level), (True, None))

    def test_a_lower_target_is_drivable(self):
        self.assertEqual(a11y.drivability("standard", "elevated"), (True, None))

    def test_a_higher_target_is_not(self):
        self.assertEqual(a11y.drivability("elevated", "standard"),
                         (False, "TARGET_NEEDS_ELEVATION"))

    def test_an_unreadable_target_needs_the_same_action_as_a_higher_one(self):
        # Windows withholds the token only when the target outranks us, so the
        # operator advice is identical and the code must match.
        self.assertEqual(a11y.drivability("unknown", "standard"),
                         (False, "TARGET_NEEDS_ELEVATION"))
        self.assertEqual(a11y.drivability("unknown", "elevated"),
                         (False, "TARGET_NEEDS_ELEVATION"))

    def test_an_unreadable_executor_is_its_own_reason(self):
        self.assertEqual(a11y.drivability("standard", "unknown"),
                         (False, "EXECUTOR_INTEGRITY_UNKNOWN"))


class OperatorCommandTests(unittest.TestCase):
    """`coord-uia` runs the adapter directly, so it must annotate too.

    The driver class annotates the receipts it builds; this command bypasses it
    and used to be the one place an operator could look at a window and get no
    verdict on whether it can be driven at all.
    """

    def test_the_report_gains_a_verdict(self):
        report = {"status": "transcript-captured", "pid": os.getpid(),
                  "input_found": True, "transcript_chars": 10}
        completed = mock.Mock(returncode=0, stdout=json.dumps(report), stderr="")
        with tempfile.TemporaryDirectory() as folder:
            args = cli.build_parser().parse_args(
                ["--coord-root", folder, "coord-uia", "capture", "--agent", "chatgpt"])
            buffer = io.StringIO()
            with mock.patch.object(cli, "uia_argv") as runner, \
                    mock.patch.object(cli, "_pwsh", return_value="pwsh"), \
                    contextlib.redirect_stdout(buffer):
                runner.run.return_value = completed
                cli.coord_uia(args)
        printed = json.loads(buffer.getvalue())["report"]
        self.assertEqual(printed["pid"], os.getpid())
        if a11y.available():
            self.assertIn("executor_elevation", printed)
            self.assertIn("drivable", printed)
        else:
            # Off Windows the annotation is a no-op, and it must stay one.
            self.assertEqual(printed, report)

    def test_a_report_without_a_window_is_left_alone(self):
        report = {"status": "NO_WINDOW"}
        completed = mock.Mock(returncode=1, stdout=json.dumps(report), stderr="")
        with tempfile.TemporaryDirectory() as folder:
            args = cli.build_parser().parse_args(
                ["--coord-root", folder, "coord-uia", "observe", "--agent", "chatgpt"])
            buffer = io.StringIO()
            with mock.patch.object(cli, "uia_argv") as runner, \
                    mock.patch.object(cli, "_pwsh", return_value="pwsh"), \
                    contextlib.redirect_stdout(buffer):
                runner.run.return_value = completed
                cli.coord_uia(args)
        self.assertEqual(json.loads(buffer.getvalue())["report"], report)

    def test_the_receipt_goes_through_the_utf8_runner(self):
        # Elevated processes report cp1252, and a non-ASCII -Text cannot survive
        # the console code page, so this command must use the same runner as the
        # driver: text=True alone made the reader thread die on the adapter's
        # UTF-8 Chinese and left stdout as None (AttributeError: 'NoneType'
        # object has no attribute 'splitlines').
        report = {"status": "transcript-captured", "pid": os.getpid()}
        completed = mock.Mock(returncode=0, stdout=json.dumps(report), stderr="")
        with tempfile.TemporaryDirectory() as folder:
            # A write-capable direct UIA invocation now requires the same
            # stable original-session marker as the profiled commander.  Do
            # not make this UTF-8 transport test silently exercise a broad
            # process-name fallback.
            profile_dir = Path(folder) / "ui_profiles"
            profile_dir.mkdir()
            (profile_dir / "chatgpt.json").write_text(
                json.dumps({"session_marker": "Original test conversation"}),
                encoding="utf-8")
            args = cli.build_parser().parse_args(
                ["--coord-root", folder, "coord-uia", "deliver", "--agent", "chatgpt",
                 "--event-id", "AEP-TEST-1", "--text", "验证 AEP-TEST-1"])
            with mock.patch.object(cli, "uia_argv") as runner, \
                    mock.patch.object(cli, "_pwsh", return_value="pwsh"), \
                    contextlib.redirect_stdout(io.StringIO()):
                runner.run.return_value = completed
                cli.coord_uia(args)
                kwargs = runner.run.call_args.kwargs
        self.assertEqual(kwargs.get("env", {}).get("AEP_SCROLL_COMPOSER"), None)
        self.assertIn("timeout", kwargs)

    def test_direct_write_refuses_an_unsettled_original_session_lease(self):
        """The raw operator CLI must not bypass the profiled commander fence."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile_dir = root / "ui_profiles"
            profile_dir.mkdir()
            marker = "Original test conversation"
            (profile_dir / "chatgpt.json").write_text(
                json.dumps({"session_marker": marker}), encoding="utf-8")
            binding = session_lease.binding(workspace_id=str(root.resolve()), provider="uia",
                                            target_agent="chatgpt", session_id=marker)
            session_lease.write_state(root, binding, event_id="AEP-OLDER-1",
                                      request_sha256="x", state="UNCERTAIN")
            args = cli.build_parser().parse_args(
                ["--coord-root", folder, "coord-uia", "deliver", "--agent", "chatgpt",
                 "--event-id", "AEP-NEW-1", "--text", "验证 AEP-NEW-1"])
            output = io.StringIO()
            with mock.patch.object(cli, "uia_argv") as runner, \
                    contextlib.redirect_stdout(output):
                cli.coord_uia(args)
            runner.run.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["report"]["status"],
                             "SESSION_UNCERTAIN")

    def test_direct_write_releases_proven_preinput_failure(self):
        """A raw NO_WINDOW receipt must not leave a fake uncertain-send fence."""
        report = {"status": "NO_WINDOW", "task_delivered": False}
        completed = mock.Mock(returncode=1, stdout=json.dumps(report), stderr="")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile_dir = root / "ui_profiles"
            profile_dir.mkdir()
            marker = "Original test conversation"
            (profile_dir / "chatgpt.json").write_text(
                json.dumps({"session_marker": marker}), encoding="utf-8")
            args = cli.build_parser().parse_args(
                ["--coord-root", folder, "coord-uia", "deliver", "--agent", "chatgpt",
                 "--event-id", "AEP-NO-WINDOW-1", "--text", "验证 AEP-NO-WINDOW-1"])
            with mock.patch.object(cli, "uia_argv") as runner, \
                    mock.patch.object(cli, "_pwsh", return_value="pwsh"), \
                    contextlib.redirect_stdout(io.StringIO()):
                runner.run.return_value = completed
                cli.coord_uia(args)
            binding = session_lease.binding(workspace_id=str(root.resolve()), provider="uia",
                                            target_agent="chatgpt", session_id=marker)
            state = session_lease.read_state(session_lease.canonical_root(root), binding,
                                             legacy_roots=(root,))
            self.assertEqual(state["state"], "PROVEN_NOT_SENT")
            self.assertFalse(session_lease.is_active(state))

    def test_an_undecodable_receipt_does_not_crash_the_command(self):
        # The reader thread can still lose a race and hand back None; the
        # command must report that, not raise AttributeError.
        completed = mock.Mock(returncode=1, stdout=None, stderr="decode failed")
        with tempfile.TemporaryDirectory() as folder:
            args = cli.build_parser().parse_args(
                ["--coord-root", folder, "coord-uia", "capture", "--agent", "chatgpt"])
            buffer = io.StringIO()
            with mock.patch.object(cli, "uia_argv") as runner, \
                    mock.patch.object(cli, "_pwsh", return_value="pwsh"), \
                    contextlib.redirect_stdout(buffer):
                runner.run.return_value = completed
                cli.coord_uia(args)
        payload = json.loads(buffer.getvalue())
        self.assertIsNone(payload["report"])
        self.assertEqual(payload["returncode"], 1)
        self.assertIn("decode failed", payload["stderr"])


class CommandTests(unittest.TestCase):
    def test_the_command_is_registered(self):
        parsed = cli.build_parser().parse_args(["coord-a11y", "status", "--pid", "4"])
        self.assertEqual(parsed.action, "status")
        self.assertEqual(parsed.pid, 4)
        self.assertFalse(parsed.persist)

    def test_status_compares_both_sides_before_saying_drivable(self):
        """The same defect had a second copy in the CLI, and it outlived the first.

        ``annotate_elevation`` was fixed to compare both integrity levels, but
        ``coord-a11y status --pid`` kept its own one-sided test -- ``elevation
        != "unknown"``.  A standard operator probing an elevated client got
        ``drivable: true`` and then every keystroke vanished into UIPI with no
        error anywhere, which is the worst shape a refusal can take.
        """
        args = cli.build_parser().parse_args(["coord-a11y", "status", "--pid", "4"])
        buffer = io.StringIO()
        with mock.patch.object(cli.a11y, "available", return_value=True), \
                mock.patch.object(cli.a11y, "status", return_value=True), \
                mock.patch.object(cli.a11y, "elevation", return_value="elevated"), \
                mock.patch.object(cli.a11y, "self_elevation", return_value="standard"), \
                contextlib.redirect_stdout(buffer):
            cli.coord_a11y(args)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["target_elevation"], "elevated")
        self.assertEqual(payload["executor_elevation"], "standard")
        self.assertFalse(payload["drivable"])
        self.assertEqual(payload["blocked_by"], "TARGET_NEEDS_ELEVATION")

    def test_status_still_clears_a_target_it_may_drive(self):
        args = cli.build_parser().parse_args(["coord-a11y", "status", "--pid", "4"])
        buffer = io.StringIO()
        with mock.patch.object(cli.a11y, "available", return_value=True), \
                mock.patch.object(cli.a11y, "status", return_value=True), \
                mock.patch.object(cli.a11y, "elevation", return_value="elevated"), \
                mock.patch.object(cli.a11y, "self_elevation", return_value="elevated"), \
                contextlib.redirect_stdout(buffer):
            cli.coord_a11y(args)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["drivable"])
        self.assertNotIn("blocked_by", payload)


class ProfileArgTests(unittest.TestCase):
    def test_a_parameter_the_caller_already_named_is_not_named_again(self):
        """PowerShell rejects a repeated parameter before the script can type.

        ``coord-uia deliver --allow-unknown-path`` on a profile that already
        sets ``allow_unknown_path`` produced a ParameterBindingException, and
        the only visible symptom was a dispatch that never happened.  Merging
        the two lists in one place removes the class rather than the instance.
        """
        profile = {"allow_unknown_path": True, "ocr_language": "zh-Hans-CN"}
        merged = ui_fallback.merge_profile_args(
            ["pwsh", "-File", "a.ps1", "-AllowUnknownPath"], profile)
        self.assertEqual(merged.count("-AllowUnknownPath"), 1)
        self.assertIn("-OcrLanguage", merged)
        self.assertEqual(merged[merged.index("-OcrLanguage") + 1], "zh-Hans-CN")

    def test_a_flag_with_a_value_keeps_the_value_next_to_it(self):
        profile = {"composer_hint": "和 Codex 一起工作", "send_mode": "enter"}
        merged = ui_fallback.merge_profile_args(["pwsh"], profile)
        self.assertEqual(merged[merged.index("-ComposerHint") + 1], "和 Codex 一起工作")
        self.assertEqual(merged[merged.index("-SendMode") + 1], "enter")

    def test_the_session_marker_travels_with_every_dispatch(self):
        # Which conversation, not just which process: the adapter refuses to
        # type when this marker is absent from a readable transcript.
        profile = {"session_marker": "赛事夺冠执行"}
        merged = ui_fallback.merge_profile_args(["pwsh"], profile)
        self.assertEqual(merged[merged.index("-SessionMarker") + 1], "赛事夺冠执行")

    def test_explicit_version_roots_travel_to_the_adapter_once(self):
        profile = {"allowed_exe_roots": [r"D:\Programs\Qoder\.qoder-versions"]}
        merged = ui_fallback.merge_profile_args(["pwsh"], profile)
        self.assertEqual(merged.count("-AllowedExeRoots"), 1)
        self.assertEqual(merged[merged.index("-AllowedExeRoots") + 1],
                         r"D:\Programs\Qoder\.qoder-versions")

    def test_the_input_is_left_alone(self):
        argv = ["pwsh", "-File", "a.ps1"]
        ui_fallback.merge_profile_args(argv, {"send_mode": "enter"})
        self.assertEqual(argv, ["pwsh", "-File", "a.ps1"])


class InterjectSafetyTests(unittest.TestCase):
    """The interject action must never be able to reach a Stop control.

    Measured on Qoder 0.5.3 while a run was live (pid 75180, 2026-11-05): the
    only action control in the composer was a Button named 停止生成 at
    1904,1968 -- no send, queue or interject button existed at all.  The old
    guard compared each label to @('停止') with ``-contains``, which is exact
    equality, so it did not recognise 停止生成; a profile that listed that label
    as an interject hint would have clicked Stop and killed the run while the
    receipt said it had interjected.  Containment is what closes it.
    """

    SOURCE = (Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol"
              / "coordination" / "wake_adapters" / "uia_session.ps1").read_text(encoding="utf-8")

    def test_every_native_target_gate_uses_the_shared_boundary_aware_matcher(self):
        adapter_dir = Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol" / "coordination" / "wake_adapters"
        matcher = (adapter_dir / "uia_path_match.ps1").read_text(encoding="utf-8")
        self.assertIn("function Test-AepExecutablePath", matcher)
        self.assertIn("[IO.Path]::GetFileName($actual)", matcher)
        self.assertIn("$actual.StartsWith($prefix", matcher)
        for name in ("uia_session.ps1", "existing_window.ps1", "uia_select_session.ps1"):
            source = (adapter_dir / name).read_text(encoding="utf-8")
            self.assertIn("uia_path_match.ps1", source)
            self.assertIn("[string[]]$AllowedExeRoots", source)

    @classmethod
    def _function(cls, name: str) -> str:
        start = cls.SOURCE.index(f"function {name} {{")
        depth = 0
        for index in range(start, len(cls.SOURCE)):
            char = cls.SOURCE[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return cls.SOURCE[start:index + 1]
        raise AssertionError(f"unbalanced braces after function {name}")

    def _label_is_stop(self, label: str) -> bool:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh is required to evaluate the adapter's own predicate")
        script = self._function("Test-AepStopLabel") + \
            f"\nTest-AepStopLabel -Name '{label}' -Words @('停止','取消','中止','Stop','Cancel','Abort')"
        completed = subprocess.run([pwsh, "-NoProfile", "-Command", script],
                                   capture_output=True, text=True, timeout=60, check=False)
        return completed.stdout.strip() == "True"

    def test_a_label_that_merely_contains_a_stop_word_is_a_stop(self):
        self.assertTrue(self._label_is_stop("停止生成"))

    def test_an_ordinary_label_is_not_a_stop(self):
        self.assertFalse(self._label_is_stop("插话"))

    def test_the_english_verb_matches_inside_a_phrase(self):
        self.assertTrue(self._label_is_stop("Stop generating"))

    def test_a_failed_promotion_says_what_the_client_offers_instead(self):
        # A refusal that only says "the hint did not match" leaves the operator
        # guessing.  Both ways a promotion can fail are reported next to
        # interject_seen -- the short, on-screen button labels the adapter
        # walked past -- so the receipt can say what the client offered instead
        # of only that the expected control was not there.
        receipt = self.SOURCE[self.SOURCE.index("interject_promoted"):]
        self.assertIn("interject_seen", receipt)
        for status in ("interject-control-not-found", "send-not-confirmed"):
            self.assertIn(f"'{status}'", self.SOURCE)

    def test_the_urgent_gate_no_longer_demands_a_control_before_the_send(self):
        # The old gate required interject_evidence.ready *before* typing, which
        # on Qoder CN 0.5.3 can never be true: the 插话 Button does not exist
        # until a message has been queued into the live run (measured
        # 2026-11-05, 1990,1618 78x30 appearing only after the send turned
        # 发送消息 into 停止生成).  A gate that can never pass is not a safety
        # property, it is a permanent outage, so its absence is asserted here.
        self.assertNotIn("interject_evidence.ready", self.SOURCE)
        self.assertIn(
            "$promoteAfterSend = [bool]($UrgentInterject -and $activity.state -eq 'busy')",
            self.SOURCE)

    def test_the_promotion_waits_instead_of_looking_once(self):
        # Looking once is exactly how "no interject channel" was concluded from
        # a client that merely shows the control late.
        body = self._function("Wait-InterjectControl")
        self.assertIn("while ([DateTime]::UtcNow -lt $deadline)", body)
        self.assertIn("Invoke-InterjectControl", body)

    def test_promoting_is_not_recorded_as_interrupting(self):
        # "interruption" means the run was killed, and the caller treats a true
        # value as a failed send.  Promotion is the opposite claim; collapsing
        # the two made a successful interject look like damage.
        self.assertIn("interruption        = $false", self.SOURCE)
        self.assertIn("interject_promoted  = $interjection", self.SOURCE)

    def test_the_send_is_retried_with_a_second_enter_before_retyping(self):
        # "Force Enter once or twice" is the measured way to send: the first
        # Enter after a ValuePattern write is swallowed because that write never
        # fired the web app's input event.  A second Enter is cheaper than
        # re-typing, so it comes first in the retry ladder.
        deliver = self.SOURCE[self.SOURCE.index("$sendAttempts = 1"):]
        second_enter = deliver.index("'enter-again'")
        repaste = deliver.index("Invoke-AepPasteClipboard -Value $Text -Replace")
        self.assertLess(second_enter, repaste)


@unittest.skipUnless(sys.platform == "win32", "Windows path matching belongs to the native adapter")
class ExecutablePathMatcherTests(unittest.TestCase):
    MATCHER = (Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol"
               / "coordination" / "wake_adapters" / "uia_path_match.ps1")

    def _match(self, actual: str, expected: str, roots: list[str]) -> dict:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh is required to evaluate the adapter's own matcher")
        root_literal = ", ".join(f"'{root}'" for root in roots)
        script = (
            f". '{self.MATCHER}'; "
            f"Test-AepExecutablePath -ActualPath '{actual}' -ExpectedPath '{expected}' "
            f"-AllowedRoots @({root_literal}) | ConvertTo-Json -Compress"
        )
        completed = subprocess.run([pwsh, "-NoProfile", "-Command", script],
                                   capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_adapter_accepts_only_a_same_named_binary_under_the_explicit_root(self):
        verdict = self._match(
            r"D:\Programs\Qoder\.qoder-versions\0.5.7\Qoder.exe",
            r"D:\Programs\Qoder\.qoder-versions\0.5.3\Qoder.exe",
            [r"D:\Programs\Qoder\.qoder-versions"])
        self.assertTrue(verdict["Match"])
        self.assertEqual(verdict["Mode"], "trusted-root")

    def test_adapter_rejects_qoder_cn_sibling_even_when_the_binary_name_matches(self):
        verdict = self._match(
            r"D:\Programs\QoderCN\.qoder-versions\0.5.7\Qoder.exe",
            r"D:\Programs\Qoder\.qoder-versions\0.5.3\Qoder.exe",
            [r"D:\Programs\Qoder"])
        self.assertFalse(verdict["Match"])
        self.assertEqual(verdict["Mode"], "path-mismatch")


if __name__ == "__main__":
    unittest.main()
