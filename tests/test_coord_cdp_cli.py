"""Direct CDP CLI writes must use an exact target and the session wrapper."""
from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol import cli


class CoordCdpCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / "coordination"

    def tearDown(self):
        self.temp.cleanup()

    def _args(self, **overrides):
        values = {
            "action": "send", "coord_root": self.root, "port": 9222,
            "title": "", "target_id": "exact-tab", "agent": "qoder",
            "text": "Inspect this", "text_file": None, "event_id": "EVT-CDP-CLI",
            "out_file": None, "preview_chars": 600, "timeout": 1,
            "workspace_id": "", "session_provider": "", "session_binding_evidence": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _profile(self):
        path = self.root / "ui_profiles" / "qoder.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
        return {
            "agent": "qoder", "session_marker": "FlagGems original chat",
            "model_marker": "DeepSeek Flash", "session_provider": "native-qoder",
            "session_binding_evidence": "verified original-session binding",
        }

    def test_send_requires_exact_target_id_before_cdp_lookup(self):
        with patch.object(cli.transport_cdp, "targets") as targets:
            with self.assertRaisesRegex(cli.ProtocolError, "exact --target-id"):
                cli.coord_cdp(self._args(target_id=""))
        targets.assert_not_called()

    def test_scan_is_observational_and_does_not_require_a_coordination_root(self):
        """Discovery must work before a workspace/profile has been created."""
        output = StringIO()
        with (patch.object(cli, "coord_root", side_effect=AssertionError("write root used")),
              patch.object(cli.transport_cdp, "scan", return_value=[{"port": 9222}]),
              redirect_stdout(output)):
            cli.coord_cdp(self._args(action="scan"))
        self.assertEqual(json.loads(output.getvalue())["endpoints"], [{"port": 9222}])

    def test_send_uses_profiled_lease_wrapper_not_raw_cdp_deliver(self):
        profile = self._profile()
        target = {"id": "exact-tab", "title": "Qoder", "ws": "ws://127.0.0.1:9222/devtools/page/exact-tab"}
        receipt = {"status": "MESSAGE_VISIBLE", "task_delivered": True,
                   "agent_accepted": False}
        output = StringIO()
        with (patch.object(cli.transport_cdp, "targets", return_value=[target]),
              patch.object(cli.ui_fallback, "load_profile", return_value=profile),
              patch.object(cli.cdp_session_delivery, "deliver", return_value=receipt) as wrapped,
              patch.object(cli.transport_cdp, "deliver") as raw,
              redirect_stdout(output)):
            cli.coord_cdp(self._args())
        value = json.loads(output.getvalue())
        self.assertEqual(value["target_id"], "exact-tab")
        self.assertEqual(value["status"], "MESSAGE_VISIBLE")
        wrapped.assert_called_once()
        raw.assert_not_called()

    def test_legacy_programmatic_namespace_gets_empty_binding_defaults(self):
        """New CLI options do not crash callers that build Namespace directly."""
        profile = self._profile()
        target = {"id": "exact-tab", "title": "Qoder", "ws": "ws://127.0.0.1:9222/devtools/page/exact-tab"}
        legacy = self._args()
        del legacy.workspace_id
        del legacy.session_provider
        del legacy.session_binding_evidence
        with (patch.object(cli.transport_cdp, "targets", return_value=[target]),
              patch.object(cli.ui_fallback, "load_profile", return_value=profile),
              patch.object(cli.cdp_session_delivery, "deliver", return_value={"status": "PROVEN_NOT_SENT"}) as wrapped,
              redirect_stdout(StringIO())):
            cli.coord_cdp(legacy)
        self.assertEqual(wrapped.call_args.kwargs["workspace_id"], "")
        self.assertEqual(wrapped.call_args.kwargs["session_provider"], "")
        self.assertEqual(wrapped.call_args.kwargs["session_binding_evidence"], "")


class FederateCompletionPolicyCliTests(unittest.TestCase):
    def test_step_passes_unattended_strict_to_supervisor(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            args = cli.build_parser().parse_args([
                "--coord-root", str(base / "coordination"), "federate", "step",
                "--federation-root", str(base / "federation"), "--workspace", "night-run",
                "--completion-policy", "unattended-strict",
            ])
            output = StringIO()
            with (patch("agent_evolution_protocol.federation.supervisor.build_adapters", return_value=[]),
                  patch("agent_evolution_protocol.federation.supervisor.step", return_value={"closed": []}) as step,
                  redirect_stdout(output)):
                args.func(args)
        self.assertEqual(json.loads(output.getvalue()), {"closed": []})
        self.assertEqual(step.call_args.kwargs["completion_policy"], "unattended-strict")


if __name__ == "__main__":
    unittest.main()
