"""Reproducible end-to-end demo of the federation kernel.

Run:  PYTHONPATH=src python examples/federation_demo.py

Scenario: a three-task DAG (collect -> analyze -> review) and four agents. The
first agent for each of the first two tasks exhausts its context mid-task, so
the kernel has to hand the task over without losing the requirement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from agent_evolution_protocol.federation import adapter, engine, snapshot, store


NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = engine.create(root, goal="produce a reviewed research note",
                              acceptance=["note exists", "review signed"],
                              constraints=["no network writes"], moment=NOW)
        engine.add_task(root, state, task_id="collect", requirement="research", moment=NOW)
        engine.add_task(root, state, task_id="analyze", requirement="code",
                        deps=["collect"], moment=NOW)
        engine.add_task(root, state, task_id="review", requirement="review",
                        deps=["analyze"], moment=NOW)

        alpha = adapter.FakeAdapter("alpha", {"research", "code"},
                                    outcomes=("token_exhausted",),
                                    heartbeat_at=NOW.isoformat())
        gamma = adapter.FakeAdapter("gamma", {"research"}, heartbeat_at=NOW.isoformat())
        beta = adapter.FakeAdapter("beta", {"code", "review"},
                                   outcomes=("token_exhausted",),
                                   heartbeat_at=NOW.isoformat())
        delta = adapter.FakeAdapter("delta", {"code", "review"}, heartbeat_at=NOW.isoformat())
        adapters = [alpha, beta, gamma, delta]

        moment = NOW
        for step, finished in enumerate(("collect", "analyze", "review"), start=1):
            moment = NOW + timedelta(minutes=step)
            report = engine.tick(root, state, adapters, now=moment, moment=moment)
            print(f"[tick {step}] {json.dumps(report['actions'], ensure_ascii=False)}")
            engine.complete(root, state, task_id=finished, moment=moment)

        workspace = store.workspace_dir(root, state["workspace_id"])
        log = store.EventLog(root, state["workspace_id"])
        events = log.events()

        print("\n[events]")
        for event in events:
            payload = json.dumps(event["payload"], ensure_ascii=False, sort_keys=True)
            print(f"{event['seq']:>4} {event['kind']:<20} {event['actor']:<8} {payload[:110]}")
        print("\nchain:", log.verify())
        print("handoffs:", [(row["task"], row["reason"], row["to_agent"]) for row in state["handoffs"]])
        print("agents:", {name: row["state"] for name, row in state["agents"].items()})
        print("tasks:", {name: row["state"] for name, row in state["tasks"].items()})
        print("snapshots:", [row["version"] for row in
                             snapshot.history(root, state["workspace_id"])])
        print("recovered:", {key: value for key, value in
                             engine.recover(root, state["workspace_id"]).items()
                             if key != "state"})
        print("event file:", workspace / "events.jsonl")


if __name__ == "__main__":
    main()
