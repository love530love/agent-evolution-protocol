"""Opt-in OSV package/version lookup. No installation, remediation or safety certification."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request


ENDPOINT = "https://api.osv.dev/v1/querybatch"
PASS_STATUS = "NO_KNOWN_VULNERABILITIES"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("OSV redirect refused")


def _query(payload):
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "AEP-dependency-audit"}, method="POST")
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("OSV response exceeds limit")
    return json.loads(body)


def _parse(response, packages):
    if not isinstance(response, dict) or not isinstance(response.get("results"), list) or len(response["results"]) != len(packages):
        raise ValueError("OSV batch result count or schema is invalid")
    rows = []
    for package, result in zip(packages, response["results"]):
        if not isinstance(result, dict):
            raise ValueError("invalid OSV package result")
        if "error" in result:
            rows.append({**package, "status": "QUERY_FAILED", "ids": [], "complete": False})
            continue
        token = result.get("next_page_token", "")
        findings = result.get("vulns", [])
        if not isinstance(token, str) or not isinstance(findings, list):
            raise ValueError("invalid OSV pagination or findings")
        ids = set()
        for finding in findings:
            if not isinstance(finding, dict) or not isinstance(finding.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", finding["id"]):
                raise ValueError("invalid OSV advisory identity")
            ids.add(finding["id"])
        rows.append({**package, "status": "INCOMPLETE" if token else "KNOWN_VULNERABILITIES" if ids else PASS_STATUS,
                     "ids": sorted(ids), "complete": not bool(token)})
    return rows


def audit(*, lock, output, apply=False, query_osv=False, query=None):
    lock, output = Path(lock), Path(output)
    if not apply:
        return {"status": "PLAN_ONLY", "lock": str(lock), "output": str(output),
                "network_requested": query_osv, "network_performed": False,
                "external_data": "PyPI package names and versions only" if query_osv else None,
                "security_verified": False, "adopted": False}
    if output.exists():
        raise ValueError("audit output already exists; no overwrite or implicit refresh")
    if lock.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("lock exceeds size limit")
    body = lock.read_bytes()
    manifest = json.loads(body)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("profile") != "desktop-win-cp313" or not isinstance(manifest.get("wheels"), list) or not 1 <= len(manifest["wheels"]) <= 120:
        raise ValueError("unsupported lock schema or size")
    packages, names = [], set()
    for row in manifest["wheels"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["name"]) or not isinstance(row.get("version"), str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", row["version"]) or row["name"] in names:
            raise ValueError("invalid or duplicate package identity")
        names.add(row["name"])
        packages.append({"name": row["name"], "version": row["version"]})
    packages.sort(key=lambda row: row["name"])
    payload = {"queries": [{"package": {"ecosystem": "PyPI", "name": row["name"]}, "version": row["version"]} for row in packages]}
    rows = [{**row, "status": "NOT_QUERIED", "ids": [], "complete": False} for row in packages]
    response, error = None, None
    status = "NOT_QUERIED"
    if query_osv:
        try:
            response = (query or _query)(payload)
            rows = _parse(response, packages)
            status = ("INCOMPLETE" if not all(row["complete"] for row in rows) else
                      "KNOWN_VULNERABILITIES" if any(row["ids"] for row in rows) else PASS_STATUS)
        except (OSError, ValueError, TypeError, TimeoutError) as exc:
            status, error = "QUERY_FAILED", type(exc).__name__
            rows = [{**row, "status": "QUERY_FAILED", "ids": [], "complete": False} for row in packages]
    report = {"schema": 1, "status": status, "at": datetime.now(timezone.utc).isoformat(),
        "lock_sha256": hashlib.sha256(body).hexdigest(), "source": ENDPOINT if query_osv else None,
        "query_attempted": query_osv, "error": error, "packages": rows,
        "complete": all(row["complete"] for row in rows),
        "finding_id_count": len({identifier for row in rows for identifier in row["ids"]}),
        "affected_package_count": sum(bool(row["ids"]) for row in rows),
        "security_verified": False, "artifact_integrity_verified": False,
        "adopted": False, "remediation_performed": False,
        "note": "Version lookup snapshot only. Empty results are not a safety certificate; advisory IDs may alias the same issue."}
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {"audit.json": report}
    if query_osv:
        artifacts["query.json"] = payload
        if response is not None:
            artifacts["response.json"] = response
    # Preserve exact JSON values received; hashes bind the serialized snapshots, not remote signatures.
    hashes = {}
    for name, value in artifacts.items():
        data = (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode()
        with (output / name).open("xb") as stream:
            stream.write(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    with (output / "checksums.json").open("x", encoding="utf-8") as stream:
        json.dump(hashes, stream, indent=2)
    return {"status": status, "output": str(output), "report": str(output / "audit.json"),
            "package_count": len(rows), "complete": report["complete"],
            "finding_id_count": report["finding_id_count"], "affected_package_count": report["affected_package_count"],
            "security_verified": False, "adopted": False}
