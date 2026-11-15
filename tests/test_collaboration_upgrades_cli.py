import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol import cli


class CollaborationUpgradesCliTests(unittest.TestCase):
    def test_new_commands_are_parseable(self):
        parser = cli.build_parser()
        cases = (
            ["coord-governance", "roles"],
            ["coord-reasoning", "status", "--task", "T1"],
            ["coord-desktop", "inventory"],
            ["coord-desktop", "capability-probe", "--agent", "qoder", "--provider", "agent_desktop"],
            ["coord-uia", "calibrate", "--agent", "qoder",
             "--session-region", "0.25,0,0.7,0.25",
             "--model-region", "0.7,0,1,0.25",
             "--selector-region", "0,0,0.25,1",
             "--composer-region", "0.25,0.75,1,1"],
            ["coord-adversarial", "status", "--task", "T1", "--submission", "S1"],
            ["coord-legion", "incentive-board"],
            ["coord-legion", "adjudicate", "--event-id", "e", "--reviewer", "r",
             "--verdict", "accepted", "--note", "proof"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertTrue(callable(parser.parse_args(argv).func))

    def test_uia_calibration_cli_writes_evidence_but_not_the_profile_without_apply(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_dir = root / "ui_profiles"
            profile_dir.mkdir()
            profile_path = profile_dir / "qoder.json"
            profile_path.write_text(json.dumps({
                "agent": "qoder", "process": "Qoder", "title": "Qoder",
                "exe": r"D:\Programs\Qoder\Qoder.exe",
                "allowed_exe_roots": [r"D:\Programs\Qoder"],
                "session_marker": "Original", "model_marker": "Model",
                "transport": "uia",
            }), encoding="utf-8")
            original = profile_path.read_bytes()

            class FakeCalibrator:
                def calibrate_route(self, profile, proof, *, select_original=False):
                    return {
                        "valid": True, "status": "CALIBRATED",
                        "session_proof": proof,
                        "surface": {
                            "kind": "desktop-uia3", "pid": 42, "title": "Qoder",
                            "actual_exe": r"D:\Programs\Qoder\Qoder.exe",
                            "path_match_mode": "exact", "path_policy_digest": "",
                            "target_id": "hwnd:1|runtime:1.2",
                        },
                        "composer_candidate_count": 1,
                        "selected_original": select_original,
                        "desktop_input_performed": False,
                        "message_visible": False,
                        "agent_ack": False,
                        "task_result_readback": False,
                    }

            parser = cli.build_parser()
            args = parser.parse_args([
                "--coord-root", str(root), "coord-uia", "calibrate", "--agent", "qoder",
                "--session-region", "0.25,0,0.7,0.25",
                "--model-region", "0.7,0,1,0.25",
                "--selector-region", "0,0,0.25,1",
                "--composer-region", "0.25,0.75,1,1",
            ])
            with patch.object(cli.uia3, "Uia3Driver", return_value=FakeCalibrator()), \
                    redirect_stdout(StringIO()) as output:
                args.func(args)
            row = json.loads(output.getvalue())
            self.assertEqual(row["status"], "CALIBRATED")
            self.assertFalse(row["profile_written"])
            self.assertFalse(row["desktop_input_performed"])
            self.assertFalse(row["message_visible"])
            self.assertFalse(row["agent_ack"])
            self.assertEqual(original, profile_path.read_bytes())
            self.assertTrue(Path(row["calibration_file"]).is_file())

    def test_raw_uia_delivery_refuses_to_bypass_a_strict_profile(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_dir = root / "ui_profiles"
            profile_dir.mkdir()
            profile = {
                "agent": "qoder", "process": "Qoder", "title": "Qoder",
                "exe": r"D:\\Programs\\Qoder\\Qoder.exe",
                "allowed_exe_roots": [r"D:\\Programs\\Qoder"],
                "session_marker": "Original", "model_marker": "Model",
                "transport": "uia", "prefer_uia3": True,
                "require_strict_session_attestation": True,
                "session_proof": {
                    "session_region": {"left": 0.25, "top": 0, "right": 0.7, "bottom": 0.25},
                    "model_region": {"left": 0.7, "top": 0, "right": 1, "bottom": 0.25},
                    "selector_region": {"left": 0, "top": 0, "right": 0.25, "bottom": 1},
                    "composer_region": {"left": 0.25, "top": 0.75, "right": 1, "bottom": 1},
                },
            }
            (profile_dir / "qoder.json").write_text(json.dumps(profile), encoding="utf-8")
            args = cli.build_parser().parse_args([
                "--coord-root", str(root), "coord-uia", "deliver", "--agent", "qoder",
                "--event-id", "EVT-STRICT-RAW", "--text", "must not reach the adapter",
            ])
            with self.assertRaisesRegex(cli.ProtocolError, "profiled commander relay"):
                args.func(args)

    def test_uia_calibration_apply_binds_profile_to_its_immutable_artifact(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_dir = root / "ui_profiles"
            profile_dir.mkdir()
            profile_path = profile_dir / "qoder.json"
            profile_path.write_text(json.dumps({
                "agent": "qoder", "process": "Qoder", "title": "Qoder",
                "exe": r"D:\\Programs\\Qoder\\Qoder.exe",
                "allowed_exe_roots": [r"D:\\Programs\\Qoder"],
                "session_marker": "Original", "model_marker": "Model",
                "transport": "uia",
            }), encoding="utf-8")

            class FakeCalibrator:
                def calibrate_route(self, profile, proof, *, select_original=False):
                    return {
                        "valid": True, "status": "CALIBRATED", "session_proof": proof,
                        "surface": {
                            "kind": "desktop-uia3", "pid": 42, "title": "Qoder",
                            "actual_exe": r"D:\\Programs\\Qoder\\Qoder.exe",
                            "path_match_mode": "exact", "path_policy_digest": "",
                            "target_id": "hwnd:1|runtime:1.2",
                        },
                        "composer_candidate_count": 1,
                        "selected_original": select_original,
                        "desktop_input_performed": False, "message_visible": False,
                        "agent_ack": False, "task_result_readback": False,
                    }

            args = cli.build_parser().parse_args([
                "--coord-root", str(root), "coord-uia", "calibrate", "--agent", "qoder",
                "--session-region", "0.25,0,0.7,0.25",
                "--model-region", "0.7,0,1,0.25",
                "--selector-region", "0,0,0.25,1",
                "--composer-region", "0.25,0.75,1,1", "--apply",
            ])
            with patch.object(cli.uia3, "Uia3Driver", return_value=FakeCalibrator()), \
                    redirect_stdout(StringIO()) as output:
                args.func(args)
            row = json.loads(output.getvalue())
            updated = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertTrue(row["profile_written"])
            self.assertTrue(Path(row["calibration_file"]).is_file())
            self.assertEqual(updated["route_calibration"]["id"], row["calibration_id"])
            self.assertEqual(updated["route_calibration"]["digest"], row["calibration_digest"])

    def test_governance_cli_only_records_an_intervention(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = root / "proof.txt"
            proof.write_text("observed", encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({"type": "metric_change", "by": "codex",
                "target": "coordination metric", "rationale": "test a different measure",
                "evidence_paths": [str(proof)], "reviewers": ["qoder"],
                "after": {"threshold": 0.5}}), encoding="utf-8")
            with redirect_stdout(StringIO()) as output:
                cli.coord_governance(SimpleNamespace(coord_root=root, action="intervene",
                                                      request_file=request))
            row = json.loads(output.getvalue())
            self.assertEqual(row["status"], "QUARANTINED")
            self.assertFalse(row["automatic_application"])
            with redirect_stdout(StringIO()) as output:
                cli.coord_governance(SimpleNamespace(coord_root=root, action="history",
                                                      request_file=None))
            self.assertEqual(json.loads(output.getvalue())[0]["id"], row["id"])
