"""Read a Windows SDK filesystem without launching its interpreter or startup hooks."""
import base64
import csv
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


PASS_STATUSES = {"LOCK_VERSIONS_MATCH", "LOCK_VERSIONS_AND_RECORDS_MATCH"}
BOOTSTRAP = {"pip", "setuptools", "wheel"}


def _inside(path, root):
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("SDK path cannot be resolved") from exc
    if not resolved.is_relative_to(root):
        raise ValueError("path escapes SDK environment")
    return resolved


def _hash(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def _record(info, site, environment):
    issues, checked, skipped = [], 0, 0
    record = _inside(info / "RECORD", environment)
    if not record.is_file() or record.stat().st_size > 8 * 1024 * 1024:
        return {"package": info.name, "issues": ["RECORD missing or too large"], "checked_files": 0, "unhashed_cache_files": 0}
    seen, hashed = set(), set()
    try:
        with record.open(encoding="utf-8", newline="") as stream:
            for index, row in enumerate(csv.reader(stream)):
                if index >= 50000 or len(row) != 3:
                    raise ValueError("invalid or oversized RECORD")
                name, encoded, size = row
                if not name or "\\" in name or ":" in name or "\x00" in name or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive:
                    raise ValueError("invalid RECORD path")
                target = _inside(site / name, environment)
                if target in seen:
                    raise ValueError("duplicate RECORD target")
                seen.add(target)
                if not encoded:
                    if target == record:
                        continue
                    if target.suffix == ".pyc":
                        skipped += 1
                        continue
                    issues.append(f"unhashed non-cache file: {name}")
                    continue
                algorithm, separator, expected = encoded.partition("=")
                if not separator or algorithm not in {"sha256", "sha384", "sha512"} or not re.fullmatch(r"[A-Za-z0-9_-]+", expected):
                    raise ValueError("unsupported RECORD hash")
                if not target.is_file():
                    issues.append(f"missing file: {name}")
                    continue
                if target.stat().st_size > 256 * 1024 * 1024:
                    issues.append(f"file exceeds inspection size limit: {name}")
                    continue
                if size and (not size.isdigit() or target.stat().st_size != int(size)):
                    issues.append(f"size mismatch: {name}")
                actual = base64.urlsafe_b64encode(_hash(target, algorithm)).decode().rstrip("=")
                if actual != expected:
                    issues.append(f"hash mismatch: {name}")
                checked += 1
                hashed.add(target)
    except (OSError, ValueError, csv.Error) as exc:
        issues.append(f"RECORD inspection failed: {type(exc).__name__}")
    if record not in seen or _inside(info / "METADATA", environment) not in hashed:
        issues.append("RECORD must include itself and hashed METADATA")
    return {"package": info.name, "issues": issues, "checked_files": checked, "unhashed_cache_files": skipped}


def inspect(*, lock, environment, output, apply=False, verify_records=False):
    lock, environment, output = map(Path, (lock, environment, output))
    if not apply:
        return {"status": "PLAN_ONLY", "lock": str(lock), "environment": str(environment),
                "output": str(output), "verify_records": verify_records,
                "interpreter_started": False, "network_performed": False}
    if output.exists():
        raise ValueError("inventory output already exists; no overwrite")
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
    environment = environment.resolve()
    if output.resolve().is_relative_to(environment):
        raise ValueError("report output must be outside the inspected SDK")
    site = _inside(environment / "Lib" / "site-packages", environment)
    if not site.is_dir() or not (environment / "pyvenv.cfg").is_file() or not (environment / "Scripts" / "python.exe").is_file():
        raise ValueError("expected a Windows SDK venv layout; interpreter is never started")
    installed, errors, records, hooks = {}, [], [], []
    infos = sorted(site.glob("*.dist-info"))
    if len(infos) > 500:
        raise ValueError("too many installed distributions")
    for info in infos:
        try:
            _inside(info, environment)
            metadata_path = _inside(info / "METADATA", environment)
            if metadata_path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("METADATA too large")
            metadata = BytesParser(policy=policy.default).parsebytes(metadata_path.read_bytes())
            if len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1:
                raise ValueError("ambiguous metadata identity")
            name = re.sub(r"[-_.]+", "-", metadata["Name"]).lower()
            version = str(metadata["Version"])
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not version.strip() or name in installed:
                raise ValueError("invalid or duplicate distribution identity")
            installed[name] = version
            if verify_records and name in expected:
                records.append(_record(info, site, environment))
        except (OSError, ValueError) as exc:
            errors.append({"directory": info.name, "error": type(exc).__name__})
    for hook in sorted([*site.glob("*.pth"), *site.glob("sitecustomize.py"), *site.glob("usercustomize.py")]):
        try:
            path = _inside(hook, environment)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("startup file too large")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            hooks.append({"file": hook.name, "sha256": _hash(path).hex(),
                          "contains_import_lines": any(line.startswith(("import ", "import\t")) for line in lines),
                          "executed": False})
        except (OSError, ValueError) as exc:
            errors.append({"directory": hook.name, "error": type(exc).__name__})
    missing = sorted(expected.keys() - installed.keys())
    mismatched = [{"name": name, "expected": expected[name], "installed": installed[name]}
                  for name in sorted(expected.keys() & installed.keys()) if expected[name] != installed[name]]
    extras = sorted(installed.keys() - expected.keys())
    bootstrap = [{"name": name, "version": installed[name]} for name in extras if name in BOOTSTRAP]
    unexpected = [name for name in extras if name not in BOOTSTRAP]
    versions_match = not (missing or mismatched or unexpected or errors)
    records_match = verify_records and versions_match and len(records) == len(expected) and not any(row["issues"] for row in records)
    status = ("ENVIRONMENT_MISMATCH" if not versions_match else "RECORD_CHECK_FAILED" if verify_records and not records_match else
              "LOCK_VERSIONS_AND_RECORDS_MATCH" if verify_records else "LOCK_VERSIONS_MATCH")
    report = {"schema": 1, "status": status, "at": datetime.now(timezone.utc).isoformat(),
        "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(), "environment": str(environment),
        "installed": installed, "missing": missing, "version_mismatches": mismatched,
        "unexpected_packages": unexpected, "bootstrap_packages_outside_lock": bootstrap,
        "metadata_errors": errors, "startup_files": hooks, "records": records,
        "lock_versions_match": versions_match, "record_hashes_match": records_match,
        "checked_files": sum(row["checked_files"] for row in records),
        "interpreter_started": False, "network_performed": False, "untracked_files_checked": False,
        "artifact_integrity_verified_against_wheels": False, "security_verified": False,
        "note": "Filesystem metadata and self-declared RECORD checks only; bootstrap packages and unmanaged code are outside the lock."}
    output.mkdir(parents=True, exist_ok=False)
    data = (json.dumps(report, ensure_ascii=True, indent=2) + "\n").encode()
    with (output / "inventory.json").open("xb") as stream:
        stream.write(data)
    with (output / "checksums.json").open("x", encoding="utf-8") as stream:
        json.dump({"inventory.json": hashlib.sha256(data).hexdigest()}, stream)
    return {key: report[key] for key in ("status", "lock_versions_match", "record_hashes_match", "checked_files", "bootstrap_packages_outside_lock", "security_verified")} | {
        "report": str(output / "inventory.json"), "missing_count": len(missing),
        "version_mismatch_count": len(mismatched), "unexpected_package_count": len(unexpected)}
