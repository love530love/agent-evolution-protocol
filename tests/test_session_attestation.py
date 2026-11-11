"""Tests for a short-lived original-session precondition.

The attestation intentionally stops before delivery.  A valid result means only
that a current surface was observed with evidence suitable for input; it is not
an INPUT_SUBMITTED, MESSAGE_VISIBLE, ACK, or result-readback claim.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agent_evolution_protocol.coordination import session_attestation


MOMENT = datetime(2026, 11, 11, 4, 0, tzinfo=timezone.utc)
FINGERPRINT = "a" * 64
PROFILE = {
    "agent": "qoder",
    "process": "Qoder",
    "title": "Qoder",
    "window_titles": ["Qoder", "Qoder - FlagGems"],
    "exe": r"D:\Programs\Qoder\Qoder.exe",
    "allowed_exe_roots": [r"D:\Programs\Qoder"],
    "session_marker": "FlagGems 原会话",
    "model_marker": "DeepSeek-Flash",
    "session_evidence_markers": ["FlagGems 原会话"],
    "model_evidence_markers": ["DeepSeek-Flash"],
    "transport": "uia",
    "workspace_id": "flaggems-sglang",
    "session_provider": "uia-session",
    "session_binding_evidence": {"profile_revision": 1},
}
SURFACE = {
    "kind": "uia",
    "pid": 4242,
    "title": "Qoder - FlagGems",
    "actual_exe": r"D:\Programs\Qoder\Qoder.exe",
    "path_match_mode": "trusted-root",
    "path_policy_digest": "sha256:" + "b" * 64,
    "target_id": "window-4242",
}
EVIDENCE = {
    "capture_sha256": "sha256:" + "c" * 64,
    "transcript_source": "uia-text-range",
    "marker_scope": "active-transcript",
    "session_marker_seen": True,
    "model_marker_seen": True,
    "session_switch": {"attempted": False, "unique": True, "result": "already-active"},
    "focus_changed": False,
    "input_attempted": False,
    "input_submitted": False,
    "evidence_path": "coordination/evidence/qoder-session.json",
}


class SessionAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def build(self, **changes):
        options = {
            "agent": "qoder",
            "profile": PROFILE,
            "observed_fingerprint": FINGERPRINT,
            "surface": SURFACE,
            "evidence": EVIDENCE,
            "moment": MOMENT,
            "strict": True,
        }
        options.update(changes)
        return session_attestation.build(**options)

    def validate(self, payload, **changes):
        options = {
            "agent": "qoder",
            "profile": PROFILE,
            "observed_fingerprint": FINGERPRINT,
            "surface": SURFACE,
            "strict": True,
            "moment": MOMENT + timedelta(seconds=1),
        }
        options.update(changes)
        return session_attestation.validate(payload, **options)

    def test_atomic_round_trip_binds_the_current_original_session(self):
        payload = self.build()
        session_attestation.write(self.root, "qoder", payload, moment=MOMENT)
        target = session_attestation.path(self.root, "qoder")
        self.assertTrue(target.is_file())
        self.assertEqual([], list(target.parent.glob("*.tmp")))
        self.assertEqual(payload, session_attestation.read(self.root, "qoder"))

        result = session_attestation.validate_stored(
            self.root,
            agent="qoder",
            profile=PROFILE,
            observed_fingerprint=FINGERPRINT,
            surface=SURFACE,
            strict=True,
            moment=MOMENT + timedelta(seconds=1),
        )
        self.assertTrue(result["valid"])
        self.assertEqual(session_attestation.REASON_VALID, result["reason"])
        self.assertFalse(result["delivery_proven"])

    def test_absent_schema_and_agent_mismatch_are_refused(self):
        absent = self.validate(None)
        self.assertFalse(absent["valid"])
        self.assertEqual(session_attestation.REASON_ABSENT, absent["reason"])

        malformed = self.build()
        malformed["schema"] = "aep-original-session-attestation-v0"
        self.assertEqual(session_attestation.REASON_SCHEMA, self.validate(malformed)["reason"])

        wrong_agent = self.build()
        wrong_agent["agent"] = "workbuddy"
        self.assertEqual(session_attestation.REASON_AGENT, self.validate(wrong_agent)["reason"])

    def test_profile_fingerprint_and_surface_changes_require_a_fresh_attestation(self):
        payload = self.build()
        different_profile = {**PROFILE, "session_marker": "另一条会话"}
        self.assertEqual(session_attestation.REASON_PROFILE,
                         self.validate(payload, profile=different_profile)["reason"])
        self.assertEqual(session_attestation.REASON_FINGERPRINT,
                         self.validate(payload, observed_fingerprint="d" * 64)["reason"])
        moved_surface = {**SURFACE, "pid": 4243}
        self.assertEqual(session_attestation.REASON_SURFACE,
                         self.validate(payload, surface=moved_surface)["reason"])

    def test_expired_future_and_malformed_timestamps_are_refused(self):
        payload = self.build(ttl_seconds=10)
        expired = self.validate(payload, moment=MOMENT + timedelta(seconds=10))
        self.assertEqual(session_attestation.REASON_EXPIRED, expired["reason"])

        future = self.build(moment=MOMENT + timedelta(seconds=1))
        self.assertEqual(session_attestation.REASON_FUTURE_TIMESTAMP,
                         self.validate(future, moment=MOMENT)["reason"])

        malformed = self.build()
        malformed["observed_at"] = "not-a-time"
        self.assertEqual(session_attestation.REASON_MALFORMED_TIMESTAMP,
                         self.validate(malformed)["reason"])

    def test_strict_validation_rejects_a_lease_longer_than_the_strict_window(self):
        payload = self.build(strict=False, ttl_seconds=session_attestation.MAX_TTL_SECONDS)
        self.assertEqual(session_attestation.REASON_MALFORMED_TIMESTAMP,
                         self.validate(payload, strict=True)["reason"])

    def test_marker_and_model_evidence_must_be_true_and_bound_to_the_profile(self):
        for key in ("session_marker_seen", "model_marker_seen"):
            evidence = {**EVIDENCE, key: False}
            with self.assertRaisesRegex(ValueError, "ATTESTATION_STRICT_EVIDENCE_INVALID"):
                self.build(evidence=evidence)

        payload = self.build()
        payload["evidence"]["marker_binding"]["model_marker_digest"] = "sha256:" + "d" * 64
        self.assertEqual(session_attestation.REASON_INVALID_STRICT_EVIDENCE,
                         self.validate(payload)["reason"])

    def test_strict_mode_refuses_weak_scope_or_post_input_observation(self):
        weak_evidence = {**EVIDENCE, "marker_scope": "whole-window"}
        observation = self.build(evidence=weak_evidence, strict=False)
        self.assertTrue(self.validate(observation, strict=False)["valid"])
        self.assertEqual(session_attestation.REASON_INVALID_STRICT_EVIDENCE,
                         self.validate(observation, strict=True)["reason"])

        after_input = {**EVIDENCE, "input_attempted": True}
        with self.assertRaisesRegex(ValueError, "ATTESTATION_STRICT_EVIDENCE_INVALID"):
            self.build(evidence=after_input)

    def test_profile_digest_is_order_independent_but_policy_sensitive(self):
        reordered = {key: PROFILE[key] for key in reversed(list(PROFILE))}
        reordered["allowed_exe_roots"] = list(reversed(PROFILE["allowed_exe_roots"]))
        self.assertEqual(
            session_attestation.profile_digest(PROFILE, agent="qoder"),
            session_attestation.profile_digest(reordered, agent="qoder"),
        )
        self.assertNotEqual(
            session_attestation.profile_digest(PROFILE, agent="qoder"),
            session_attestation.profile_digest({**PROFILE, "transport": "cdp"}, agent="qoder"),
        )

    def test_profile_digest_refuses_a_whole_volume_trust_root(self):
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            session_attestation.profile_digest(
                {**PROFILE, "allowed_exe_roots": ["D:\\"]}, agent="qoder")

    def test_corrupt_stored_json_is_absent_and_unsafe_agent_names_are_rejected(self):
        target = session_attestation.path(self.root, "qoder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not json", encoding="utf-8")
        self.assertIsNone(session_attestation.read(self.root, "qoder"))
        with self.assertRaises(ValueError):
            session_attestation.path(self.root, "../workbuddy")

    def test_write_refuses_an_expired_payload(self):
        payload = self.build(ttl_seconds=1)
        with self.assertRaisesRegex(ValueError, "ATTESTATION_EXPIRED"):
            session_attestation.write(self.root, "qoder", payload,
                                      moment=MOMENT + timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
