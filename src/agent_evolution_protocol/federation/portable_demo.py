"""Run the federation contract against two local simulated agents."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from . import engine, store
from .adapter import FakeAdapter


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix="aep-demo-", dir=output.resolve()))
    now = datetime.now(timezone.utc)
    state = engine.create(
        root, goal="Produce and independently verify a local artifact",
        acceptance=["Producer and reviewer finish with a verified event log"],
        moment=now,
    )
    workspace = state["workspace_id"]
    engine.add_task(root, state, task_id="produce", requirement="Write the artifact",
                    capability="produce", moment=now)
    engine.add_task(root, state, task_id="review", requirement="Verify the artifact",
                    capability="review", deps=["produce"], moment=now)
    adapters = [
        FakeAdapter("demo-producer", {"produce"}, heartbeat_at=now.isoformat()),
        FakeAdapter("demo-reviewer", {"review"}, heartbeat_at=now.isoformat()),
    ]

    artifact = root / "artifact.txt"
    expected = b"AEP portable federation demo\n"
    owners = {"produce": "demo-producer", "review": "demo-reviewer"}
    for task_id, owner in owners.items():
        state = engine.load(root, workspace)
        engine.tick(root, state, adapters, now=now, moment=now)
        task = state["tasks"][task_id]
        if task["state"] != "running" or task["owner"] != owner:
            raise ValueError(f"demo assignment failed: {task_id}")
        if task_id == "produce":
            artifact.write_bytes(expected)
        elif artifact.read_bytes() != expected:
            raise ValueError("demo artifact verification failed")
        engine.complete(root, state, task_id=task_id, artifact_path=artifact, moment=now)

    state = engine.load(root, workspace)
    if not store.EventLog(root, workspace).verify()["valid"]:
        raise ValueError("demo event log verification failed")
    if any(task["state"] != "done" for task in state["tasks"].values()):
        raise ValueError("demo did not persist completion")
    return {
        "schema": "aep-portable-demo-v1",
        "mode": "simulated",
        "workspace_id": workspace,
        "run_directory": str(root),
        "artifact": artifact.name,
        "event_log_verified": True,
        "tasks": {name: {"owner": task["owner"], "state": task["state"]}
                  for name, task in state["tasks"].items()},
    }
