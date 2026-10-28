"""Evidence-driven provider/agent capability scoring."""
from __future__ import annotations
from typing import Any
import json
from pathlib import Path

def record(metrics: dict[str, Any], provider: str, *, success: bool, latency_ms: int,
           evidence_quality: float = 1.0, failure_signature: str = "") -> dict[str, Any]:
    if not provider or isinstance(latency_ms, bool) or latency_ms < 0: raise ValueError("invalid metric")
    if not 0 <= evidence_quality <= 1: raise ValueError("evidence_quality must be 0..1")
    row = metrics.setdefault(provider, {"attempts": 0, "successes": 0, "latency_ms": [], "evidence": [], "failures": {}})
    row["attempts"] += 1
    row["successes"] += int(success)
    row["latency_ms"].append(latency_ms)
    row["evidence"].append(evidence_quality)
    if failure_signature: row["failures"][failure_signature] = row["failures"].get(failure_signature, 0) + 1
    return metrics

def score(row: dict[str, Any]) -> float:
    attempts = max(1, int(row.get("attempts", 0)))
    success = row.get("successes", 0) / attempts
    evidence = sum(row.get("evidence", [0])) / max(1, len(row.get("evidence", [])))
    latency = sum(row.get("latency_ms", [60000])) / max(1, len(row.get("latency_ms", [])))
    return round(success * 0.6 + evidence * 0.3 + max(0.0, 1 - latency / 60000) * 0.1, 6)

def rank(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(({"provider": name, "score": score(row), "attempts": row.get("attempts", 0), "top_failure": max(row.get("failures", {}), key=row.get("failures", {}).get) if row.get("failures") else ""} for name, row in metrics.items()), key=lambda x: (-x["score"], x["provider"]))

def save(path: Path, metrics: dict[str, Any]) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "aep-capability-metrics-v1", "metrics": metrics}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def load(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "aep-capability-metrics-v1" or not isinstance(payload.get("metrics"), dict):
        raise ValueError("invalid capability metrics schema")
    return payload["metrics"]
