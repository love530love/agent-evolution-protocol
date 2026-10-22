"""Inspect wheels as data and publish review candidates, never install or adopt."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request
import zipfile


WHEEL = re.compile(r"^([A-Za-z0-9_]+)-([0-9]+(?:\.[0-9]+)*)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)\.whl$")
URL = re.compile(r"^https://files\.pythonhosted\.org/packages/[A-Za-z0-9/_.+-]+\.whl$")


def _normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch(name, version, timeout):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=timeout) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("PyPI response exceeds limit")
    return json.loads(body)


def _compatible(python, abi, platform):
    if platform not in {"any", "win_amd64"}:
        return False
    if abi == "none" and set(python.split(".")) & {"py3", "py313", "cp313"}:
        return True
    if platform != "win_amd64":
        return False
    return (python == "cp313" and abi == "cp313" or
            abi == "abi3" and bool(re.fullmatch(r"cp3[0-9]{1,2}", python)) and int(python[2:]) <= 313)


def _metadata(path):
    match = WHEEL.fullmatch(path.name)
    if not match or not _compatible(*match.groups()[2:]):
        raise ValueError(f"unsupported Windows CPython 3.13 wheel: {path.name}")
    name, version = _normal(match[1]), match[2]
    with zipfile.ZipFile(path) as archive:
        entries = [item for item in archive.infolist() if item.filename.endswith(".dist-info/METADATA")]
        if len(entries) != 1 or entries[0].file_size > 2 * 1024 * 1024 or entries[0].flag_bits & 1:
            raise ValueError("wheel must contain one bounded METADATA entry")
        if entries[0].compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("unsupported wheel metadata compression")
        expected = f"{match[1]}-{version}.dist-info/METADATA"
        if entries[0].filename != expected:
            raise ValueError("wheel metadata directory differs from filename")
        metadata = BytesParser().parsebytes(archive.read(entries[0]))
    if _normal(metadata.get("Name", "")) != name or metadata.get("Version") != version:
        raise ValueError("wheel filename and METADATA identity differ")
    return name, version, {"name": name, "version": version,
        "requires_dist": metadata.get_all("Requires-Dist", []),
        "license_expression": metadata.get("License-Expression"),
        "license_declaration": metadata.get("License"),
        "license_files": metadata.get_all("License-File", []),
        "license_review_completed": False}


def build(*, baseline, wheelhouse, output, apply=False, verify_pypi=False, fetch=None):
    """Offline reuse requires exact baseline hashes; changed artifacts need opt-in PyPI verification."""
    baseline, wheelhouse, output = map(Path, (baseline, wheelhouse, output))
    if not apply:
        return {"status": "PLAN_ONLY", "baseline": str(baseline), "wheelhouse": str(wheelhouse),
                "output": str(output), "network_requested": verify_pypi,
                "network_performed": False, "installed": False, "adopted": False}
    if output.exists():
        raise ValueError("candidate output already exists; no overwrite")
    baseline_bytes = baseline.read_bytes()
    base = json.loads(baseline_bytes)
    if not isinstance(base, dict) or base.get("profile") != "desktop-win-cp313" or base.get("root") != "mcp==1.30.0" or not isinstance(base.get("wheels"), list):
        raise ValueError("unsupported baseline profile")
    prior = {}
    for row in base["wheels"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or row["name"] in prior:
            raise ValueError("invalid or duplicate baseline wheel")
        prior[row["name"]] = row
    paths = sorted(wheelhouse.iterdir())
    if not paths or len(paths) > 120 or any(not path.is_file() or path.is_symlink() or not WHEEL.fullmatch(path.name) for path in paths):
        raise ValueError("wheelhouse requires 1..120 wheel files only")
    rows, declarations, proofs, names = [], [], [], set()
    deadline = time.monotonic() + 90
    for path in paths:
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("wheel exceeds inspection size limit")
        name, version, declaration = _metadata(path)
        if name in names:
            raise ValueError("multiple wheels for one distribution")
        names.add(name)
        digest = _digest(path)
        old = prior.get(name, {})
        if verify_pypi:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("PyPI verification budget exhausted")
            metadata = (fetch or _fetch)(name, version, min(15, remaining))
            if not isinstance(metadata, dict) or not isinstance(metadata.get("urls"), list):
                raise ValueError("invalid PyPI release metadata")
            matches = [row for row in metadata.get("urls", []) if isinstance(row, dict) and row.get("filename") == path.name]
            if len(matches) != 1 or not isinstance(matches[0].get("digests"), dict) or matches[0]["digests"].get("sha256") != digest or matches[0].get("yanked") is not False:
                raise ValueError("PyPI file hash, uniqueness or non-yanked status unverified")
            url = matches[0].get("url")
            proof = "pypi-json-sha256"
        elif old.get("filename") == path.name and old.get("sha256") == digest and old.get("version") == version:
            url, proof = old.get("url"), "existing-baseline-sha256"
        else:
            raise ValueError("changed artifact requires explicit --verify-pypi; no network was attempted")
        if not isinstance(url, str) or not URL.fullmatch(url) or url.rsplit("/", 1)[-1] != path.name:
            raise ValueError("artifact URL is not a matching PyPI wheel")
        rows.append({"name": name, "version": version, "filename": path.name, "sha256": digest, "url": url})
        declarations.append(declaration)
        proofs.append({"name": name, "verification": proof})
    if not any(row["name"] == "mcp" and row["version"] == "1.30.0" for row in rows):
        raise ValueError("candidate must retain pinned MCP 1.30.0 root")
    current = {row["name"]: row for row in rows}
    added, removed = sorted(current.keys() - prior.keys()), sorted(prior.keys() - current.keys())
    changed = [{"name": name, "before": prior[name], "after": current[name]}
               for name in sorted(prior.keys() & current.keys()) if prior[name] != current[name]]
    candidate = {key: base[key] for key in ("schema", "profile", "python", "platform", "root", "source")}
    candidate["wheels"] = sorted(rows, key=lambda row: row["name"])
    body = (json.dumps(candidate, ensure_ascii=True, indent=2) + "\n").encode()
    report = {"status": "CANDIDATE_ONLY", "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "candidate_sha256": hashlib.sha256(body).hexdigest(), "added": added, "removed": removed,
        "changed": changed, "artifacts": proofs, "declarations": declarations,
        "dependency_closure_verified": False, "license_review_completed": False,
        "vulnerability_scan_performed": False, "installed": False, "adopted": False,
        "required_gates": ["isolated offline install", "pip check", "native imports", "workflow acceptance", "independent review", "explicit Git adoption"]}
    # Reserve exclusively after every artifact passed. Partial output is evidence, never auto-adopted.
    output.mkdir(parents=True, exist_ok=False)
    with (output / "candidate.lock.json").open("xb") as stream:
        stream.write(body)
    with (output / "review.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
    return {**report, "output": str(output)}
