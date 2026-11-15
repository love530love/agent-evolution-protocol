"""Regression coverage for read-only client route calibration."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import client_route_calibration as calibration
from agent_evolution_protocol.coordination import session_attestation
from agent_evolution_protocol.coordination import ui_fallback


MOMENT = datetime(2026, 11, 13, 5, 30, tzinfo=timezone.utc)
PROFILE = {
    "agent": "qoder", "process": "Qoder", "title": "Qoder",
    "exe": r"D:\Programs\Qoder\Qoder.exe",
    "allowed_exe_roots": [r"D:\Programs\Qoder"],
    "session_marker": "FlagGems original", "model_marker": "DeepSeek-Flash",
    "transport": "uia", "send_mode": "enter",
}
PROOF = {
    "session_region": {"left": 0.25, "top": 0.0, "right": 0.70, "bottom": 0.25},
    "model_region": {"left": 0.70, "top": 0.0, "right": 1.0, "bottom": 0.25},
    "selector_region": {"left": 0.0, "top": 0.0, "right": 0.25, "bottom": 1.0},
    "composer_region": {"left": 0.25, "top": 0.75, "right": 1.0, "bottom": 1.0},
}
SURFACE = {
    "kind": "desktop-uia3", "pid": 4242, "title": "Qoder",
    "actual_exe": r"D:\Programs\Qoder\Qoder.exe",
    "path_match_mode": "exact", "path_policy_digest": "",
    "target_id": "hwnd:77|runtime:42.77",
}


class ClientRouteCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile_path = self.root / "ui_profiles" / "qoder.json"
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")

    @staticmethod
    def observation(**extra):
        return {
            "valid": True, "status": "CALIBRATED", "surface": SURFACE,
            "session_proof": PROOF,
            "composer_candidate_count": 1,
            "desktop_input_performed": False,
            "message_visible": False, "agent_ack": False,
            "task_result_readback": False,
            **extra,
        }

    def build(self, **changes):
        options = {
            "agent": "qoder", "profile": PROFILE, "session_proof": PROOF,
            "observation": self.observation(), "moment": MOMENT,
        }
        options.update(changes)
        return calibration.build(**options)

    def test_valid_calibration_is_explicitly_not_a_delivery_receipt(self):
        payload = self.build()
        checked = calibration.validate(payload, agent="qoder")
        self.assertTrue(checked["valid"])
        self.assertFalse(payload["desktop_input_performed"])
        self.assertFalse(payload["message_visible"])
        self.assertFalse(payload["agent_ack"])
        self.assertFalse(payload["task_result_readback"])
        self.assertFalse(payload["delivery_verified"])

    def test_invalid_observation_cannot_create_or_write_a_calibration(self):
        with self.assertRaisesRegex(ValueError, "invalid route calibration"):
            self.build(observation=self.observation(valid=False))
        self.assertFalse(calibration.directory(self.root, "qoder").exists())

    def test_write_and_apply_are_separate_and_profile_update_is_atomic(self):
        original = self.profile_path.read_bytes()
        payload = self.build()
        calibration.write(self.root, payload)
        self.assertTrue(calibration.path(self.root, "qoder", payload["id"]).is_file())
        self.assertEqual(original, self.profile_path.read_bytes())

        applied = calibration.apply_profile(self.root, self.profile_path, payload)
        self.assertTrue(applied["prefer_uia3"])
        self.assertTrue(applied["require_strict_session_attestation"])
        self.assertEqual(applied["session_proof"], PROOF)
        self.assertEqual(applied["route_calibration"]["id"], payload["id"])
        self.assertEqual(applied["route_calibration"]["digest"], payload["digest"])
        self.assertEqual([], list(self.profile_path.parent.glob("*.calibration.json")))
        reloaded = ui_fallback.load_profile(self.profile_path)
        self.assertEqual(reloaded["route_calibration"]["session_proof_digest"],
                         calibration.proof_digest(PROOF))

    def test_apply_treats_omitted_optional_markers_as_their_normalised_policy(self):
        """A CLI calibration and --apply must agree on sparse source JSON."""
        # These fields are commonly omitted from handwritten profiles.  The
        # loader normalizes both to [], so their omission is not a route drift.
        sparse = {**PROFILE}
        self.profile_path.write_text(json.dumps(sparse), encoding="utf-8")
        payload = calibration.build(
            agent="qoder", profile=ui_fallback.load_profile(self.profile_path),
            session_proof=PROOF, observation=self.observation(), moment=MOMENT)
        calibration.write(self.root, payload)
        applied = calibration.apply_profile(self.root, self.profile_path, payload)
        self.assertEqual(applied["session_evidence_markers"], [])
        self.assertEqual(applied["model_evidence_markers"], [])
        self.assertTrue(calibration.validate_profile_reference(self.root, applied)["valid"])

    def test_tampered_delivery_field_is_rejected(self):
        payload = self.build()
        payload["message_visible"] = True
        self.assertFalse(calibration.validate(payload)["valid"])

    def test_build_rejects_an_observation_that_does_not_prove_this_route(self):
        mismatched = {**PROOF, "composer_region": {
            "left": 0.30, "top": 0.75, "right": 1.0, "bottom": 1.0,
        }}
        with self.assertRaisesRegex(ValueError, "proof does not match"):
            self.build(observation=self.observation(session_proof=mismatched))
        with self.assertRaisesRegex(ValueError, "delivery evidence"):
            self.build(observation=self.observation(message_visible=True))
        with self.assertRaisesRegex(ValueError, "selection evidence"):
            self.build(selected_original=True)

    def test_apply_refuses_a_profile_changed_since_calibration(self):
        payload = self.build()
        calibration.write(self.root, payload)
        drifted = {**PROFILE, "model_marker": "Another model"}
        expected = json.dumps(drifted).encode("utf-8")
        self.profile_path.write_bytes(expected)
        with self.assertRaisesRegex(ValueError, "changed since route calibration"):
            calibration.apply_profile(self.root, self.profile_path, payload)
        self.assertEqual(expected, self.profile_path.read_bytes())

    def test_active_strict_profile_must_match_the_persisted_calibration(self):
        payload = self.build()
        calibration.write(self.root, payload)
        applied = calibration.apply_profile(self.root, self.profile_path, payload)
        result = calibration.validate_profile_reference(self.root, applied)
        self.assertTrue(result["valid"])
        self.assertTrue(result["required"])

        missing = calibration.path(self.root, "qoder", payload["id"])
        missing.unlink()
        result = calibration.validate_profile_reference(self.root, applied)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "CALIBRATION_ARTIFACT_MISSING")

    def test_new_unapplied_calibration_does_not_invalidate_the_active_route(self):
        first = self.build()
        calibration.write(self.root, first)
        applied_first = calibration.apply_profile(self.root, self.profile_path, first)
        self.assertTrue(calibration.validate_profile_reference(self.root, applied_first)["valid"])

        second = calibration.build(
            agent="qoder", profile=ui_fallback.load_profile(self.profile_path),
            session_proof=PROOF, observation=self.observation(), moment=MOMENT)
        calibration.write(self.root, second)
        self.assertTrue(calibration.path(self.root, "qoder", first["id"]).is_file())
        self.assertTrue(calibration.path(self.root, "qoder", second["id"]).is_file())
        # Merely recording B leaves the active profile reference to A usable.
        self.assertTrue(calibration.validate_profile_reference(self.root, applied_first)["valid"])

        applied_second = calibration.apply_profile(self.root, self.profile_path, second)
        self.assertTrue(calibration.validate_profile_reference(self.root, applied_second)["valid"])
        self.assertTrue(calibration.path(self.root, "qoder", first["id"]).is_file())

    def test_apply_requires_the_exact_persisted_artifact_without_mutating_profile(self):
        original = self.profile_path.read_bytes()
        payload = self.build()
        with self.assertRaisesRegex(ValueError, "artifact is missing"):
            calibration.apply_profile(self.root, self.profile_path, payload)
        self.assertEqual(original, self.profile_path.read_bytes())

    def test_calibration_id_cannot_be_reused_for_different_payload(self):
        first = self.build()
        calibration.write(self.root, first)
        conflicting = dict(first)
        conflicting["surface_digest"] = "sha256:" + "f" * 64
        unsigned = dict(conflicting)
        unsigned.pop("digest")
        conflicting["digest"] = calibration._sha256(unsigned)
        with self.assertRaisesRegex(ValueError, "already exists with a different payload"):
            calibration.write(self.root, conflicting)

    def test_calibration_reference_changes_the_attestation_policy_digest(self):
        payload = self.build()
        calibration.write(self.root, payload)
        applied = calibration.apply_profile(self.root, self.profile_path, payload)
        original = session_attestation.profile_digest(applied, agent="qoder")
        changed = {**applied, "route_calibration": {
            **applied["route_calibration"], "digest": "sha256:" + "f" * 64,
        }}
        self.assertNotEqual(original, session_attestation.profile_digest(changed, agent="qoder"))


if __name__ == "__main__":
    unittest.main()
