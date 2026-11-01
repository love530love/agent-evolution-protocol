"""SRE 审计复现脚本。只写临时目录，不碰仓库状态。

用法：
    PYTHONPATH=src python docs/audit/repro/sre/repro.py <case>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from agent_evolution_protocol.federation import engine  # noqa: E402
from agent_evolution_protocol.federation.store import EventLog, workspace_dir  # noqa: E402

REPO = Path(__file__).resolve().parents[4]


def fresh(tag):
    base = Path(tempfile.mkdtemp(prefix=f"aep-{tag}-"))
    (base / "coord").mkdir()
    (base / "fed").mkdir()
    return base


def make_workspace(base, tasks=1):
    state = engine.create(base / "fed", goal="sre", acceptance=["ok"])
    ws = state["workspace_id"]
    for i in range(tasks):
        engine.add_task(base / "fed", state, task_id=f"t{i:04d}",
                        requirement=f"task {i}", capability="execute")
    engine.save(base / "fed", state)
    return ws


def cli(*args, env=None):
    e = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    if env:
        e.update(env)
    return subprocess.run([sys.executable, "-m", "agent_evolution_protocol", *args],
                          capture_output=True, text=True, encoding="utf-8", env=e, timeout=600)


# ---------------------------------------------------------------- case: kill9
def case_kill9():
    base = fresh("kill9")
    ws = make_workspace(base, tasks=60)
    log = (base / "serve.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_evolution_protocol",
         "--coord-root", str(base / "coord"), "federate", "serve",
         "--federation-root", str(base / "fed"), "--workspace", ws,
         "--agents", "alpha", "--no-dispatch", "--interval", "1"],
        stdout=log, stderr=subprocess.STDOUT,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    print(f"spawned serve pid={proc.pid}")
    time.sleep(4)
    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
    print("killed with taskkill /F (SIGKILL equivalent)")
    time.sleep(1)

    wdir = workspace_dir(base / "fed", ws)
    print("\n--- leftover files in workspace dir ---")
    for p in sorted(wdir.iterdir()):
        print(f"  {p.name}  {p.stat().st_size} bytes")

    st = wdir / "state.json"
    try:
        json.loads(st.read_text(encoding="utf-8"))
        print("state.json: parses OK")
    except Exception as exc:
        print(f"state.json: BROKEN -> {type(exc).__name__}: {exc}")

    try:
        rep = EventLog(base / "fed", ws).verify()
        print(f"events.jsonl verify: {rep}")
    except Exception as exc:
        print(f"events.jsonl verify: RAISED -> {type(exc).__name__}: {exc}")

    print("\n--- restart after kill -9 ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "status",
            "--federation-root", str(base / "fed"), "--workspace", ws, "--agents", "alpha")
    print(f"exit={r.returncode}")
    print((r.stdout or r.stderr)[:400])
    shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------- case: truncated tail
def case_truncated_tail():
    base = fresh("trunc")
    ws = make_workspace(base, tasks=3)
    path = workspace_dir(base / "fed", ws) / "events.jsonl"
    before = path.read_text(encoding="utf-8").splitlines()
    print(f"log has {len(before)} events")
    print(f"tail: {before[-1][:160]}")
    # 模拟断电/磁盘满导致最后一行只写了一半
    with path.open("a", encoding="utf-8") as fh:
        fh.write(before[-1][: len(before[-1]) // 2])
    print("appended half a line to simulate a torn write")

    print("\n--- federate status ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "status",
            "--federation-root", str(base / "fed"), "--workspace", ws, "--agents", "alpha")
    print(f"exit={r.returncode}\n{(r.stdout or '')[:300]}\nSTDERR: {(r.stderr or '')[:500]}")

    print("\n--- federate serve (one tick) ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "serve",
            "--federation-root", str(base / "fed"), "--workspace", ws,
            "--agents", "alpha", "--no-dispatch", "--max-ticks", "1")
    print(f"exit={r.returncode}\nSTDOUT: {(r.stdout or '')[:200]}\nSTDERR: {(r.stderr or '')[-800:]}")

    print("\n--- can I repair it with the CLI? ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "revive",
            "--federation-root", str(base / "fed"), "--workspace", ws,
            "--task", "t0000", "--by", "sre", "--reason", "x")
    print(f"exit={r.returncode}\nSTDERR: {(r.stderr or '')[-600:]}")
    shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------ case: read-only
def case_readonly():
    """模拟磁盘满：目标目录拒绝写入。"""
    base = fresh("ro")
    ws = make_workspace(base, tasks=1)
    wdir = workspace_dir(base / "fed", ws)
    target = wdir / "state.json"
    print(f"workspace: {wdir}")
    # Windows 上 chmod 不能真的只读；用 ACL 拒绝写入来模拟磁盘满
    subprocess.run(["icacls", str(wdir), "/deny", f"{os.environ.get('USERNAME')}:(W,AD)"],
                   capture_output=True)
    print("icacls deny write applied")

    r = cli("--coord-root", str(base / "coord"), "federate", "serve",
            "--federation-root", str(base / "fed"), "--workspace", ws,
            "--agents", "alpha", "--no-dispatch", "--max-ticks", "1")
    print(f"serve exit={r.returncode}")
    print(f"STDOUT: {(r.stdout or '')[:400]}")
    print(f"STDERR: {(r.stderr or '')[-1200:]}")

    subprocess.run(["icacls", str(wdir), "/remove:d", os.environ.get("USERNAME", "")],
                   capture_output=True)
    shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------- case: clock
def case_clock():
    """NTP 把时钟往回拨 10 分钟。"""
    from datetime import datetime, timedelta, timezone
    from agent_evolution_protocol.federation import supervisor

    sent_at = datetime.now(timezone.utc).isoformat()
    real_now = datetime.now(timezone.utc)
    rolled_back = real_now - timedelta(minutes=10)

    for label, now in (("clock normal (+30min later)", real_now + timedelta(minutes=30)),
                       ("clock rolled back 10 min", rolled_back)):
        value = {"sent": True, "state": "SENT", "sent_at": sent_at,
                 "event_id": "EVT-X", "order_id": "ORD-X"}
        # 直接调用内部判据，构造一个 "已发送 30 分钟未 ACK" 的单
        orig = supervisor._seconds_since
        waited = orig(sent_at, now)
        verdict = {"waited": waited,
                   "over_limit": waited is not None and waited >= 3600}
        print(f"{label}: unacked_for_seconds={waited} over_limit={verdict['over_limit']}")

    print("\n--- hold_alerts 看到负数会怎样 ---")
    from agent_evolution_protocol.coordination import hold_alerts
    row = {"task": "execute", "over_limit": True, "unacked_for_seconds": -600,
           "limit_seconds": 3600, "sent_at": sent_at, "event_id": "EVT-X", "state": "SENT"}
    base = fresh("clock")
    (base / "coord" / "commander" / "inbox").mkdir(parents=True)
    rep = hold_alerts.emit(base / "coord", [row])
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:800])
    shutil.rmtree(base, ignore_errors=True)


# ----------------------------------------------------------------- case: scale
def case_scale():
    base = fresh("scale")
    t0 = time.perf_counter()
    ws = make_workspace(base, tasks=1000)
    t1 = time.perf_counter()
    print(f"create workspace with 1000 tasks: {t1 - t0:.2f}s")

    log = EventLog(base / "fed", ws)
    t0 = time.perf_counter()
    for i in range(300):
        log.append("scale_probe", actor="sre", payload={"i": i, "pad": "x" * 200})
    t1 = time.perf_counter()
    n = len(log.events())
    print(f"append 300 more events (total {n}): {t1 - t0:.2f}s  -> {1000*(t1-t0)/300:.1f} ms/append")

    t0 = time.perf_counter()
    r = cli("--coord-root", str(base / "coord"), "federate", "status",
            "--federation-root", str(base / "fed"), "--workspace", ws, "--agents", "alpha")
    t1 = time.perf_counter()
    print(f"`federate status` wall clock: {t1 - t0:.2f}s  exit={r.returncode}  "
          f"output={len(r.stdout)} bytes")

    t0 = time.perf_counter()
    r = cli("--coord-root", str(base / "coord"), "federate", "serve",
            "--federation-root", str(base / "fed"), "--workspace", ws,
            "--agents", "alpha", "--no-dispatch", "--max-ticks", "1")
    t1 = time.perf_counter()
    print(f"one `serve` tick wall clock: {t1 - t0:.2f}s exit={r.returncode}")

    sz = (workspace_dir(base / "fed", ws) / "events.jsonl").stat().st_size
    print(f"events.jsonl size: {sz/1024:.0f} KiB")
    print(f"state.json size: {(workspace_dir(base / 'fed', ws) / 'state.json').stat().st_size/1024:.0f} KiB")
    shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------- case: blocked
def case_blocked():
    base = fresh("blocked")
    ws = make_workspace(base, tasks=2)
    state = engine.load(base / "fed", ws)
    # 制造三种 blocked：no capable agent / dep failed / abandoned
    state["tasks"]["t0000"].update({"state": "blocked", "note": "no capable agent"})
    state["tasks"]["t0001"].update({"state": "blocked", "note": "blocked by ['t0000']",
                                    "blocked_by_deps": ["t0000"]})
    engine.save(base / "fed", state)

    print("--- revive a task blocked for 'no capable agent' ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "revive",
            "--federation-root", str(base / "fed"), "--workspace", ws,
            "--task", "t0000", "--by", "sre", "--reason", "fixing")
    print(f"exit={r.returncode}\n{r.stdout or ''}{r.stderr or ''}")

    print("--- revive a task blocked by a dependency ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "revive",
            "--federation-root", str(base / "fed"), "--workspace", ws,
            "--task", "t0001", "--by", "sre", "--reason", "fixing")
    print(f"exit={r.returncode}\n{r.stdout or ''}{r.stderr or ''}")

    print("--- what does the CLI offer for a non-abandonment block? ---")
    r = cli("federate", "--help")
    print(r.stdout[r.stdout.find("action"):][:200] if "action" in r.stdout else r.stdout[:400])
    shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------- case: manual edit
def case_manual_edit():
    """手改 state.json 会不会破坏 hash chain？"""
    base = fresh("edit")
    ws = make_workspace(base, tasks=1)
    print("--- verify before ---")
    print(EventLog(base / "fed", ws).verify())

    st_path = workspace_dir(base / "fed", ws) / "state.json"
    st = json.loads(st_path.read_text(encoding="utf-8"))
    st["tasks"]["t0000"]["state"] = "done"
    st_path.write_text(json.dumps(st, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                       encoding="utf-8")
    print("hand-edited state.json: t0000 -> done")

    print("--- verify after (event chain) ---")
    print(EventLog(base / "fed", ws).verify())
    print("--- does anything notice the state contradicts the log? ---")
    r = cli("--coord-root", str(base / "coord"), "federate", "status",
            "--federation-root", str(base / "fed"), "--workspace", ws, "--agents", "alpha")
    print(f"exit={r.returncode}  {r.stdout[:200]}")
    shutil.rmtree(base, ignore_errors=True)


# ----------------------------------------------------------------- case: logs
def case_logs():
    """随机抽 20 行 events.jsonl，看能不能读懂。"""
    import random
    base = fresh("logs")
    ws = make_workspace(base, tasks=3)
    log = EventLog(base / "fed", ws)
    for kind, actor, payload in (
            ("task_blocked", "kernel", {"task": "t0002", "reason": "no capable agent"}),
            ("handoff_blocked", "kernel", {"task": "t0000", "reason": "stalled"}),
            ("workspace_created", "kernel", {"goal": "sre"}),
    ):
        log.append(kind, actor=actor, payload=payload)
    rows = log.events()
    print(f"total events: {len(rows)}")
    for row in random.sample(rows, min(6, len(rows))):
        print(json.dumps(row, ensure_ascii=False)[:520])
        print()
    shutil.rmtree(base, ignore_errors=True)


CASES = {"kill9": case_kill9, "truncated": case_truncated_tail, "readonly": case_readonly,
         "clock": case_clock, "scale": case_scale, "blocked": case_blocked,
         "edit": case_manual_edit, "logs": case_logs}

if __name__ == "__main__":
    CASES[sys.argv[1]]()
