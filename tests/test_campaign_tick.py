from pathlib import Path
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import campaign_tick as batch


class BatchTickTests(unittest.TestCase):
    def test_optional_failure_does_not_skip_required_deadline_reconcile(self):
        with patch.object(batch.presence, "reconcile", return_value={}), \
             patch.object(batch.team_dispatch, "process", side_effect=ValueError("poison request")), \
             patch.object(batch.command_memory, "ingest_journal", return_value={}) as ingest, \
             patch.object(batch.campaign_cycle, "reconcile", return_value={"ok": True}) as reconcile:
            result = batch.tick(Path("coord"), Path("bridge"))
        self.assertEqual(result["warnings"][0]["stage"], "delegation")
        self.assertEqual(ingest.call_count, 2)
        reconcile.assert_called_once()
        self.assertEqual(result["campaign"], {"ok": True})

    def test_deadlines_compare_offsets_not_strings(self):
        with patch.object(batch.presence, "next_due", return_value="2026-10-20T10:00:00+08:00"), \
             patch.object(batch.campaign_cycle, "next_due", return_value="2026-10-20T03:00:00+00:00"):
            self.assertEqual(batch.next_due(Path("coord"))["next_due_at"], "2026-10-20T10:00:00+08:00")
