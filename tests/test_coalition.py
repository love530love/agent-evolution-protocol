from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.coalition import capacity, dispatch, planner, spec
from agent_evolution_protocol.coordination import presence


POLICY = {
    "max_roles_per_vendor": 2,
    "max_critical_roles_per_vendor": 1,
    "allow_single_vendor": False,
    "require_backup": True,
    "backup_depth": 2,
    "allow_degraded": True,
    "allow_vendor_overflow": True,
    "max_primary_roles_per_agent": 2,
}


def role(role_id, required, *, criticality="normal", acting=None,
         allow_scribe=True, preferred_vendor="", write_scope=("tests",)):
    return {
        "id": role_id,
        "title": role_id,
        "criticality": criticality,
        "required_capabilities": list(required),
        **({"acting_capabilities": list(acting)} if acting is not None else {}),
        "allow_scribe_fallback": allow_scribe,
        "preferred_vendor": preferred_vendor,
        "write_scope": list(write_scope),
    }


def mission(agents, roles, holders=None, *, policy=None, name="test"):
    return {
        "schema": spec.SCHEMA,
        "mission": name,
        "description": "test mission",
        "vendor_policy": policy or POLICY,
        "agents": [
            {"agent": item[0], "vendor": item[1], "capabilities": list(item[2]),
             "max_primary_roles": item[3] if len(item) > 3 else 2,
             "subagent_slots": 1}
            for item in agents
        ],
        "roles": roles,
        "current_holders": holders or {},
    }


class CoalitionTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.coord = self.root / "coord"
        (self.coord / "presence" / "agents").mkdir(parents=True)
        self.evidence = self.root / "evidence.txt"
        self.evidence.write_text("durable test evidence\n", encoding="utf-8")
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self._temp.cleanup()

    def signal(self, agent, *, state="online", ttl=120, source="test"):
        return presence.signal(self.coord, agent=agent, state=state, source=source,
                               ttl_minutes=ttl)

    def capacity(self, agent, state, *, ttl=120, now=None):
        return capacity.record(
            self.coord, agent=agent, vendor="v-" + agent, state=state,
            source="test", evidence=[str(self.evidence)], ttl_minutes=ttl,
            now=now or self.now)

    def test_ready_plan_keeps_holders_and_cross_vendor_backups(self):
        spec_obj = mission(
            [
                ("alpha", "va", ["command"]),
                ("beta", "vb", ["command"]),
                ("gamma", "vc", ["intel"]),
                ("delta", "vd", ["intel"]),
            ],
            [
                role("command", ["command"], criticality="critical",
                     allow_scribe=False),
                role("intel", ["intel"], allow_scribe=False),
            ],
            {"command": "alpha", "intel": "gamma"},
        )
        for agent in ("alpha", "beta", "gamma", "delta"):
            self.signal(agent)
            self.capacity(agent, "AVAILABLE")
        report = planner.plan(self.coord, spec_obj, now=self.now)
        self.assertEqual(report["verdict"], "READY")
        command = report["roles"]["command"]
        self.assertEqual(command["primary"]["agent"], "alpha")
        self.assertEqual(command["tick"]["action"], "HOLD")
        self.assertEqual(command["coverage"], "COVERED")
        self.assertEqual([item["agent"] for item in command["backups"]], ["beta"])
        self.assertEqual(report["tick"]["count"], 0)
        self.assertFalse(any(item["severity"] in ("critical", "high")
                             for item in report["risks"]))

    def test_quota_exhaustion_promotes_cross_vendor_backup(self):
        spec_obj = mission(
            [
                ("alpha", "va", ["command"]),
                ("beta", "vb", ["command"]),
                ("epsilon", "ve", ["command"]),
                ("gamma", "vc", ["intel"]),
                ("delta", "vd", ["intel"]),
            ],
            [
                role("command", ["command"], criticality="critical",
                     allow_scribe=False),
                role("intel", ["intel"], allow_scribe=False),
            ],
            {"command": "alpha", "intel": "gamma"},
        )
        for agent in ("alpha", "beta", "epsilon", "gamma", "delta"):
            self.signal(agent)
            self.capacity(agent, "EXHAUSTED" if agent == "alpha" else "AVAILABLE")
        report = planner.plan(self.coord, spec_obj, now=self.now)
        command = report["roles"]["command"]
        self.assertEqual(command["tick"]["action"], "TAKEOVER_NOW")
        self.assertEqual(command["tick"]["from"], "alpha")
        self.assertEqual(command["tick"]["target"], "beta")
        self.assertEqual(command["tick"]["trigger"], "quota_exhausted")
        self.assertTrue(command["tick"]["execute_allowed"])
        self.assertEqual(command["primary"]["agent"], "beta")
        self.assertEqual([item["agent"] for item in command["backups"]], ["epsilon"])
        self.assertEqual(report["verdict"], "READY")
        actions = planner.tick(self.coord, spec_obj, now=self.now)["actions"]
        self.assertEqual([item["action"] for item in actions], ["TAKEOVER_NOW"])
        preview = dispatch.preview(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", now=self.now)
        self.assertTrue(preview["guard"]["ok"])
        self.assertTrue(preview["guard"]["execute_allowed"])
        self.assertFalse(preview["executed"])

    def test_stale_presence_requires_verification_not_takeover(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        self.signal("alpha", ttl=1)
        self.signal("beta", ttl=120)
        self.capacity("alpha", "AVAILABLE", ttl=300)
        self.capacity("beta", "AVAILABLE", ttl=300)
        later = self.now + timedelta(minutes=2)
        report = planner.plan(self.coord, spec_obj, now=later)
        command = report["roles"]["command"]
        self.assertEqual(command["holder"]["presence"], "stale")
        self.assertEqual(command["coverage"], "UNVERIFIED")
        self.assertEqual(command["tick"]["action"], "VERIFY_HOLDER")
        self.assertFalse(command["tick"]["execute_allowed"])
        self.assertEqual(report["verdict"], "BLOCKED_UNTIL_WAKE")
        preview = dispatch.preview(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", now=later)
        self.assertIn("ACTION_NOT_EXECUTABLE:VERIFY_HOLDER",
                      preview["guard"]["problems"])
        self.assertFalse(preview["guard"]["execute_allowed"])
        self.assertFalse((self.coord / "delegation" / "inbox").exists())

    def test_weak_agent_can_scribe_but_not_irreversible_role(self):
        spec_obj = mission(
            [("weak", "local", [])],
            [
                role("record", ["report"], allow_scribe=True),
                role("packaging", ["packaging"], criticality="critical",
                     allow_scribe=False),
            ],
        )
        self.signal("weak")
        self.capacity("weak", "AVAILABLE")
        report = planner.plan(self.coord, spec_obj, now=self.now)
        record = report["roles"]["record"]
        self.assertEqual(record["tick"]["action"], "ASSIGN_PRIMARY")
        self.assertEqual(record["tick"]["target"], "weak")
        self.assertEqual(record["tick"]["authority"], "scribe")
        self.assertTrue(record["tick"]["execute_allowed"])
        self.assertEqual(record["coverage"], "DEGRADED")
        packaging = report["roles"]["packaging"]
        self.assertEqual(packaging["tick"]["action"], "BLOCKED")
        self.assertFalse(packaging["tick"]["execute_allowed"])
        self.assertEqual(report["verdict"], "BLOCKED_NO_CANDIDATE")
        codes = {item["code"] for item in report["risks"]}
        self.assertIn("SCRIBE_FALLBACK", codes)
        self.assertIn("ROLE_BLOCKED", codes)

    def test_vendor_concentration_is_a_risk_not_a_silent_success(self):
        policy = dict(POLICY, max_roles_per_vendor=1,
                      max_critical_roles_per_vendor=1,
                      allow_vendor_overflow=True)
        spec_obj = mission(
            [("alpha", "va", ["command", "build"], 2)],
            [
                role("build", ["build"], criticality="critical",
                     allow_scribe=False),
                role("command", ["command"], criticality="critical",
                     allow_scribe=False),
            ],
            policy=policy,
        )
        self.signal("alpha")
        self.capacity("alpha", "AVAILABLE")
        report = planner.plan(self.coord, spec_obj, now=self.now)
        self.assertEqual(report["roles"]["build"]["primary"]["agent"], "alpha")
        self.assertEqual(report["roles"]["command"]["primary"]["agent"], "alpha")
        self.assertEqual(report["verdict"], "DEGRADED")
        codes = {item["code"] for item in report["risks"]}
        self.assertIn("VENDOR_OVERFLOW", codes)
        self.assertIn("SINGLE_VENDOR", codes)

    def test_capacity_freshness_corruption_and_atomic_write(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.capacity("alpha", "AVAILABLE", ttl=1, now=base)
        expired = capacity.read(self.coord, "alpha", now=base + timedelta(minutes=2))
        self.assertEqual(expired["state"], "UNKNOWN")
        self.assertEqual(expired["reason"], "EVIDENCE_EXPIRED")
        self.assertEqual(expired["recorded_state"], "AVAILABLE")
        self.assertFalse(expired["runnable"])
        missing = capacity.read(self.coord, "beta", now=base)
        self.assertEqual(missing["reason"], "NO_EVIDENCE")
        path = self.coord / capacity.DIRECTORY / "alpha.json"
        path.write_text("{not json", encoding="utf-8")
        corrupt = capacity.read(self.coord, "alpha", now=base)
        self.assertEqual(corrupt["state"], "UNKNOWN")
        self.assertEqual(corrupt["reason"], "CORRUPT_RECORD")
        self.assertEqual(list(path.parent.glob(".tmp-*")), [])
        fresh = self.capacity("alpha", "AVAILABLE", ttl=30, now=base)
        self.assertEqual(fresh["state"], "AVAILABLE")
        self.assertTrue(capacity.read(self.coord, "alpha", now=base)["runnable"])
        with self.assertRaises(ValueError):
            self.capacity("alpha", "BROKEN")
        with self.assertRaises(ValueError):
            self.capacity("alpha", "AVAILABLE", ttl=0)

    def test_guarded_dispatch_is_one_shot_and_ack_aware(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        for agent in ("alpha", "beta", "sender"):
            self.signal(agent)
            self.capacity(agent, "EXHAUSTED" if agent == "alpha" else "AVAILABLE")
        preview = dispatch.preview(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", now=self.now)
        self.assertTrue(preview["guard"]["execute_allowed"])
        self.assertFalse((self.coord / "delegation" / "inbox").exists())

        record = dispatch.takeover(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", sender="sender",
            evidence=[str(self.evidence)], execute=True, now=self.now)
        self.assertIn(record["state"], ("MAILBOX_ONLY", "WAKE_QUEUED_UNVERIFIED",
                                        "RELAY_QUEUED_UNVERIFIED"))
        self.assertFalse(record["agent_accepted"])
        self.assertEqual(record["delivery_claim"], "NONE")
        self.assertTrue(record["request_id"].startswith("D"))
        self.assertTrue(Path(record["message_path"]).is_file())
        inbox = list((self.coord / "delegation" / "inbox").glob("*.json"))
        self.assertEqual(len(inbox), 1)

        again = dispatch.takeover(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", sender="sender",
            evidence=[str(self.evidence)], execute=True, now=self.now)
        self.assertTrue(again["idempotent"])
        self.assertFalse(again["retry_allowed"])
        self.assertEqual(again["request_id"], record["request_id"])
        self.assertEqual(len(list((self.coord / "delegation" / "inbox").glob("*.json"))), 1)

        listed = dispatch.status(self.coord)
        self.assertEqual(len(listed["records"]), 1)
        self.assertEqual(listed["records"][0]["key"], record["key"])

        acked = dispatch.acknowledge(
            self.coord, key=record["key"], agent="beta", session_id="",
            reply="已接单；先验证写入范围。", accepted=True)
        self.assertEqual(acked["state"], "AGENT_ACK")
        self.assertTrue(acked["agent_accepted"])
        reconciled = dispatch.reconcile(self.coord, key=record["key"])
        self.assertEqual(reconciled["records"][0]["state"], "AGENT_ACK")

        with self.assertRaises(ValueError):
            dispatch.takeover(
                self.coord, spec_obj, role="command", to_agent="beta",
                trigger="offline", sender="sender",
                evidence=[str(self.evidence)], execute=True, now=self.now)
        self.assertEqual(len(list((self.coord / "delegation" / "inbox").glob("*.json"))), 1)

    def test_capacity_status_ignores_stray_temporary_files(self):
        self.capacity("alpha", "AVAILABLE")
        folder = self.coord / capacity.DIRECTORY
        (folder / ".tmp-leftover.json").write_text("{}", encoding="utf-8")
        report = capacity.status(self.coord, now=self.now)
        self.assertEqual(set(report["agents"]), {"alpha"})
        self.assertEqual(report["counts"]["AVAILABLE"], 1)

    def test_malformed_presence_row_fails_closed_without_crashing(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        self.signal("beta")
        self.capacity("beta", "AVAILABLE")
        row = {"agent": "alpha", "state": "online", "source": "test",
               "expires_at": "2099-01-01T00:00:00"}
        (self.coord / "presence" / "agents" / "alpha.json").write_text(
            json.dumps(row), encoding="utf-8")
        report = planner.plan(self.coord, spec_obj, now=self.now)
        command = report["roles"]["command"]
        self.assertIn(command["holder"]["presence"], ("stale", "unknown"))
        self.assertEqual(command["tick"]["action"], "VERIFY_HOLDER")
        self.assertFalse(command["tick"]["execute_allowed"])

    def test_corrupt_handoff_record_refuses_to_redispatch(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        for agent in ("alpha", "beta", "sender"):
            self.signal(agent)
            self.capacity(agent, "EXHAUSTED" if agent == "alpha" else "AVAILABLE")
        record = dispatch.takeover(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", sender="sender",
            evidence=[str(self.evidence)], execute=True, now=self.now)
        record_path = self.coord / dispatch.DIRECTORY / f"{record['key']}.json"
        record_path.write_text("{corrupt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HANDOFF_RECORD_UNREADABLE"):
            dispatch.takeover(
                self.coord, spec_obj, role="command", to_agent="beta",
                trigger="quota_exhausted", sender="sender",
                evidence=[str(self.evidence)], execute=True, now=self.now)
        self.assertEqual(
            len(list((self.coord / "delegation" / "inbox").glob("*.json"))), 1)

    def test_pre_dispatch_failure_can_retry_the_same_key(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        for agent in ("alpha", "beta"):
            self.signal(agent)
            self.capacity(agent, "EXHAUSTED" if agent == "alpha" else "AVAILABLE")
        with self.assertRaisesRegex(ValueError, "DISPATCH_NOT_SENT"):
            dispatch.takeover(
                self.coord, spec_obj, role="command", to_agent="beta",
                trigger="quota_exhausted", sender="sender",
                evidence=[str(self.evidence)], execute=True, now=self.now)
        self.assertFalse((self.coord / "delegation" / "inbox").exists())
        failed = dispatch.status(self.coord)["records"][0]
        self.assertEqual(failed["state"], "NOT_DISPATCHED")
        self.assertTrue(failed["retry_allowed"])

        self.signal("sender")
        retried = dispatch.takeover(
            self.coord, spec_obj, role="command", to_agent="beta",
            trigger="quota_exhausted", sender="sender",
            evidence=[str(self.evidence)], execute=True, now=self.now)
        self.assertNotIn("idempotent", retried)
        self.assertIn(retried["state"], ("MAILBOX_ONLY", "WAKE_QUEUED_UNVERIFIED",
                                         "RELAY_QUEUED_UNVERIFIED"))
        self.assertEqual(
            len(list((self.coord / "delegation" / "inbox").glob("*.json"))), 1)

    def test_plan_digest_is_reproducible_and_input_sensitive(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        for agent in ("alpha", "beta"):
            self.signal(agent)
            self.capacity(agent, "AVAILABLE")
        first = planner.plan(self.coord, spec_obj, now=self.now)
        second = planner.plan(self.coord, spec_obj, now=self.now)
        self.assertEqual(first["input_digest"], second["input_digest"])
        self.assertEqual(first["plan_digest"], second["plan_digest"])
        self.assertEqual(planner.tick(self.coord, spec_obj, now=self.now)["plan_digest"],
                         first["plan_digest"])
        self.capacity("beta", "LOW")
        changed = planner.plan(self.coord, spec_obj, now=self.now)
        self.assertNotEqual(changed["plan_digest"], first["plan_digest"])

    def test_active_session_is_advisory_and_cannot_replace_holder(self):
        spec_obj = mission(
            [("alpha", "va", ["command"]), ("beta", "vb", ["command"])],
            [role("command", ["command"], criticality="critical",
                  allow_scribe=False)],
            {"command": "alpha"},
        )
        spec_obj["active_sessions"] = {
            "command": {
                "agent": "beta",
                "session_id": "beta-1",
                "observed_at": self.now.isoformat(),
                "evidence": "coordination/messages/beta/1.json",
                "acknowledged": False,
                "note": "observed active session",
            }
        }
        for agent in ("alpha", "beta"):
            self.signal(agent)
            self.capacity(agent, "AVAILABLE")
        report = planner.plan(self.coord, spec_obj, now=self.now)
        command = report["roles"]["command"]
        self.assertEqual(command["primary"]["agent"], "alpha")
        self.assertEqual(command["tick"]["action"], "HOLD")
        declared = report["active_sessions"]["command"]
        self.assertEqual(declared["agent"], "beta")
        self.assertTrue(declared["advisory_only"])
        self.assertFalse(declared["authority_transfer"])
        codes = {item["code"] for item in report["risks"]}
        self.assertIn("ACTIVE_SESSION_NOT_HOLDER", codes)
        self.assertIn("ACTIVE_SESSION_ACK_UNVERIFIED", codes)
        self.assertNotIn("UNDECLARED_ACTIVE_SESSION", codes)

        spec_obj["active_sessions"]["command"]["agent"] = "gamma"
        spec_obj["active_sessions"]["command"]["acknowledged"] = True
        second = planner.plan(self.coord, spec_obj, now=self.now)
        self.assertEqual(second["roles"]["command"]["primary"]["agent"], "alpha")
        codes = {item["code"] for item in second["risks"]}
        self.assertIn("UNDECLARED_ACTIVE_SESSION", codes)
        self.assertNotIn("ACTIVE_SESSION_ACK_UNVERIFIED", codes)

    def test_example_spec_and_fail_closed_validation(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "coalition-flagos.json"
        loaded = spec.load_spec(example)
        self.assertEqual(loaded["mission"], "flagos")
        self.assertEqual(len(loaded["roles"]), 5)
        self.assertEqual(loaded["active_sessions"]["commander"]["agent"],
                         "workbuddyai")
        self.assertFalse(loaded["active_sessions"]["commander"]["acknowledged"])
        self.assertIn("workbuddyai",
                      {item["agent"] for item in loaded["agents"]})
        base = mission([("alpha", "va", ["command"])],
                       [role("command", ["command"])])
        duplicate = json.loads(json.dumps(base))
        duplicate["agents"].append(dict(duplicate["agents"][0]))
        with self.assertRaises(ValueError):
            spec.load_spec(duplicate)
        bad_scope = json.loads(json.dumps(base))
        bad_scope["roles"][0]["write_scope"] = ["../escape"]
        with self.assertRaises(ValueError):
            spec.load_spec(bad_scope)
        bad_holder = json.loads(json.dumps(base))
        bad_holder["current_holders"] = {"missing": "alpha"}
        with self.assertRaises(ValueError):
            spec.load_spec(bad_holder)
        bad_schema = json.loads(json.dumps(base))
        bad_schema["schema"] = "other"
        with self.assertRaises(ValueError):
            spec.load_spec(bad_schema)
        bad_active_role = json.loads(json.dumps(base))
        bad_active_role["active_sessions"] = {
            "missing": {"agent": "alpha", "evidence": "mailbox:1"}}
        with self.assertRaises(ValueError):
            spec.load_spec(bad_active_role)
        bad_active_evidence = json.loads(json.dumps(base))
        bad_active_evidence["active_sessions"] = {
            "command": {"agent": "alpha", "evidence": ""}}
        with self.assertRaises(ValueError):
            spec.load_spec(bad_active_evidence)
        bad_active_ack = json.loads(json.dumps(base))
        bad_active_ack["active_sessions"] = {
            "command": {"agent": "alpha", "evidence": "mailbox:1",
                        "acknowledged": "yes"}}
        with self.assertRaises(ValueError):
            spec.load_spec(bad_active_ack)


if __name__ == "__main__":
    unittest.main()
