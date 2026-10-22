"""Join immutable dependency evidence snapshots without querying or deploying."""
from datetime import datetime, timezone
import hashlib
import json
import ntpath
from pathlib import Path
import re

from . import dependency_audit
from .dependency_inventory import BOOTSTRAP


def _bundle(directory, filename, required=()):
    directory = Path(directory).resolve()
    index_path = directory / "checksums.json"
    if index_path.is_symlink() or not index_path.resolve().is_relative_to(directory) or index_path.stat().st_size > 65536:
        raise ValueError("checksum index too large")
    index_bytes = index_path.read_bytes()
    index = json.loads(index_bytes)
    if not isinstance(index, dict) or not 1 <= len(index) <= 20 or not {filename, *required}.issubset(index):
        raise ValueError("required evidence checksums missing")
    values, total = {}, 0
    for name, digest in index.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", name) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("unsafe checksum entry")
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory) or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("linked, escaped or oversized evidence")
        body = path.read_bytes()
        total += len(body)
        if total > 24 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("evidence hash or size mismatch")
        values[name] = json.loads(body)
    if not isinstance(values[filename], dict):
        raise ValueError("report must be a JSON object")
    return values, {"directory": str(directory), "checksums_sha256": hashlib.sha256(index_bytes).hexdigest(), "files": index}


def _fresh(report, now, hours):
    stamp = datetime.fromisoformat(report["at"])
    if stamp.tzinfo is None:
        raise ValueError("evidence timestamp lacks timezone")
    age = (now - stamp).total_seconds()
    if age < -300 or age > hours * 3600:
        raise ValueError("evidence is expired or dated in the future")


def _installed(report, expected):
    actual = report.get("installed")
    if not isinstance(actual, dict) or any(not isinstance(version, str) or not version for version in actual.values()) or any(actual.get(name) != version for name, version in expected.items()) or any(name not in expected and name not in BOOTSTRAP for name in actual):
        raise ValueError("installed identities differ from lock")
    if report.get("lock_versions_match") is not True or any(report.get(field) != [] for field in ("missing", "version_mismatches", "unexpected_packages", "metadata_errors")):
        raise ValueError("installed evidence contains unresolved differences")
    return actual


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def evaluate(*, lock, audit_dir, inventory_dir, wheel_dir, output, apply=False, max_age_hours=24, now=None):
    if isinstance(max_age_hours, bool) or not isinstance(max_age_hours, int) or not 1 <= max_age_hours <= 168:
        raise ValueError("max age must be 1..168 hours")
    output = Path(output)
    if not apply:
        return {"status": "PLAN_ONLY", "output": str(output), "max_age_hours": max_age_hours,
                "network_performed": False, "deployment_authorized": False}
    if output.exists():
        raise ValueError("gate output already exists; no overwrite")
    for directory in (audit_dir, inventory_dir, wheel_dir):
        if output.resolve().is_relative_to(Path(directory).resolve()):
            raise ValueError("gate output must be outside source evidence bundles")
    lock = Path(lock)
    if lock.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("lock too large")
    lock_bytes = lock.read_bytes()
    manifest = json.loads(lock_bytes)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("profile") != "desktop-win-cp313" or not isinstance(manifest.get("wheels"), list) or not 1 <= len(manifest["wheels"]) <= 120:
        raise ValueError("unsupported lock profile")
    expected = {}
    for row in manifest["wheels"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["name"]) or not isinstance(row.get("version"), str) or not row["version"] or row["name"] in expected:
            raise ValueError("invalid or duplicate lock identity")
        expected[row["name"]] = row["version"]
    digest = hashlib.sha256(lock_bytes).hexdigest()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("gate clock must be timezone aware")
    reports, snapshots, errors = {}, {}, []
    definitions = (("audit", audit_dir, "audit.json", ("query.json", "response.json")),
        ("inventory", inventory_dir, "inventory.json", ()),
        ("wheel", wheel_dir, "wheel-comparison.json", ()),
        ("wheel_inventory", Path(wheel_dir) / "inventory", "inventory.json", ()))
    for label, directory, filename, required in definitions:
        try:
            values, snapshot = _bundle(directory, filename, required)
            snapshots[label] = snapshot
            report = values[filename]
            if report.get("schema") != 1 or report.get("lock_sha256") != digest:
                raise ValueError("evidence lock hash or schema differs")
            _fresh(report, now, max_age_hours)
            reports[label] = (report, values)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            errors.append({"source": label, "reason": type(exc).__name__ + ": " + str(exc)[:240]})
    try:
        if len(reports) != 4:
            raise ValueError("one or more required evidence bundles are invalid")
        audit, raw = reports["audit"]
        packages = [{"name": name, "version": expected[name]} for name in sorted(expected)]
        query = {"queries": [{"package": {"ecosystem": "PyPI", "name": row["name"]}, "version": row["version"]} for row in packages]}
        parsed = dependency_audit._parse(raw["response.json"], packages)
        if raw["query.json"] != query or audit.get("source") != dependency_audit.ENDPOINT or audit.get("query_attempted") is not True or audit.get("status") != dependency_audit.PASS_STATUS or audit.get("complete") is not True or audit.get("packages") != parsed or audit.get("finding_id_count") != 0 or any(row["ids"] or not row["complete"] for row in parsed):
            raise ValueError("OSV evidence is incomplete, inconsistent or reports known findings")
        installed, _ = reports["inventory"]
        actual = _installed(installed, expected)
        records = installed.get("records")
        if installed.get("status") != "LOCK_VERSIONS_AND_RECORDS_MATCH" or installed.get("record_hashes_match") is not True or not isinstance(records, list) or len(records) != len(expected) or any(not isinstance(row, dict) or row.get("issues") != [] or not _count(row.get("checked_files")) for row in records) or installed.get("checked_files") != sum(row["checked_files"] for row in records):
            raise ValueError("installed RECORD evidence is incomplete")
        wheel, _ = reports["wheel"]
        rows = wheel.get("packages")
        if wheel.get("status") != "WHEEL_PAYLOADS_MATCH" or wheel.get("wheel_payloads_match") is not True or not isinstance(rows, list) or len(rows) != len(expected) or any(not isinstance(row, dict) or row.get("issues") != [] or row.get("unsupported") != [] or row.get("wheel_sha256_matches") is not True or not _count(row.get("checked_files")) for row in rows) or {row.get("name") for row in rows} != set(expected) or wheel.get("checked_files") != sum(row["checked_files"] for row in rows):
            raise ValueError("wheel payload evidence is incomplete")
        nested, _ = reports["wheel_inventory"]
        if nested.get("status") != "LOCK_VERSIONS_MATCH" or _installed(nested, expected) != actual or not isinstance(installed.get("environment"), str) or not installed["environment"] or not isinstance(nested.get("environment"), str) or ntpath.normcase(ntpath.normpath(installed["environment"])) != ntpath.normcase(ntpath.normpath(nested["environment"])):
            raise ValueError("inventory snapshots refer to different environments or installed identities")
        if not isinstance(wheel.get("environment"), str) or ntpath.normcase(ntpath.normpath(wheel["environment"])) != ntpath.normcase(ntpath.normpath(installed["environment"])) or wheel.get("inventory_sha256") != snapshots["wheel_inventory"]["files"]["inventory.json"]:
            raise ValueError("wheel report does not bind this environment and preflight snapshot")
    except (ValueError, KeyError, TypeError) as exc:
        errors.append({"source": "cross-check", "reason": str(exc)[:240]})
    status = "EVIDENCE_BLOCKED" if errors else "SCOPED_EVIDENCE_CONSISTENT"
    report = {"schema": 1, "status": status, "at": now.isoformat(), "lock_sha256": digest,
        "max_age_hours": max_age_hours, "sources": snapshots, "errors": errors,
        "network_performed": False, "interpreter_started": False, "deployment_authorized": False,
        "security_verified": False, "identity_authenticated": False,
        "excluded_scope": ["bootstrap packages outside lock", "generated wrappers and untracked files", "source code and interpreter provenance", "license and legal review", "actual deployment and Agent ACK"],
        "note": "Evidence consistency snapshot only; not a deployment, safety or identity approval."}
    output.mkdir(parents=True, exist_ok=False)
    body = (json.dumps(report, ensure_ascii=True, indent=2) + "\n").encode()
    with (output / "gate.json").open("xb") as stream:
        stream.write(body)
    with (output / "checksums.json").open("x", encoding="utf-8") as stream:
        json.dump({"gate.json": hashlib.sha256(body).hexdigest()}, stream)
    return {"status": status, "report": str(output / "gate.json"), "error_count": len(errors),
            "deployment_authorized": False, "security_verified": False}
