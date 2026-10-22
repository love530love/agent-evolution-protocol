"""Compare locked wheel payloads with installed files; never extract or execute."""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile

from . import dependency_inventory as inventory
from .dependency_candidate import WHEEL, _compatible


def _parts(name):
    if "\\" in name or ":" in name or "\x00" in name or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("unsafe wheel member path")
    return name.split("/")


def _target(name, prefix, site, environment):
    parts = _parts(name)
    if parts[0].endswith(".data"):
        if parts[0] != prefix + ".data" or len(parts) < 3:
            raise ValueError("unexpected wheel data directory")
        roots = {"purelib": site, "platlib": site, "scripts": environment / "Scripts", "data": environment}
        if parts[1] not in roots:
            return None  # Unsupported mapping must remain incomplete, never silently pass.
        target = roots[parts[1]].joinpath(*parts[2:])
    else:
        target = site.joinpath(*parts)
    return inventory._inside(target, environment)


def _package(row, wheelhouse, environment, owners):
    result = {"name": row["name"], "wheel_sha256_matches": False, "checked_files": 0,
              "installer_mutable": [], "unsupported": [], "issues": []}
    filename = row.get("filename")
    match = WHEEL.fullmatch(filename) if isinstance(filename, str) else None
    if not match or re.sub(r"[-_.]+", "-", match[1]).lower() != row["name"] or match[2] != row["version"] or not _compatible(*match.groups()[2:]) or not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
        raise ValueError("invalid locked wheel filename, platform or digest")
    path = wheelhouse / filename
    if path.is_symlink() or not path.resolve().is_relative_to(wheelhouse) or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("wheel is missing, linked, outside wheelhouse or oversized")
    site = inventory._inside(environment / "Lib/site-packages", environment)
    prefix = f"{match[1]}-{match[2]}"
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        if digest.hexdigest() != row["sha256"]:
            raise ValueError("wheel does not match locked SHA256")
        result["wheel_sha256_matches"] = True
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > 50000 or sum(entry.file_size for entry in entries) > 256 * 1024 * 1024:
                raise ValueError("wheel inventory exceeds inspection budget")
            if not {prefix + ".dist-info/" + name for name in ("METADATA", "WHEEL", "RECORD")}.issubset({entry.filename for entry in entries}):
                raise ValueError("required wheel metadata is missing")
            metadata_entries = [entry for entry in entries if entry.filename.endswith(".dist-info/METADATA")]
            if len(metadata_entries) != 1 or metadata_entries[0].filename != prefix + ".dist-info/METADATA" or metadata_entries[0].file_size > 2 * 1024 * 1024:
                raise ValueError("wheel metadata identity is ambiguous")
            if metadata_entries[0].flag_bits & 1 or metadata_entries[0].compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("unsupported metadata encoding")
            metadata = BytesParser(policy=policy.default).parsebytes(archive.read(metadata_entries[0]))
            if len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1 or re.sub(r"[-_.]+", "-", metadata["Name"]).lower() != row["name"] or str(metadata["Version"]) != row["version"]:
                raise ValueError("wheel metadata differs from lock")
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    raise ValueError("sanitized or ambiguous ZIP member name")
                if entry.is_dir():
                    _parts(entry.filename.rstrip("/"))
                    continue
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("linked wheel members are unsupported")
                target = _target(entry.filename, prefix, site, environment)
                if target is None:
                    result["unsupported"].append(entry.filename)
                    continue
                key = str(target).casefold()  # Windows target collisions also tested on Linux.
                if key in owners:
                    raise ValueError("wheel members collide at an installed target")
                owners[key] = row["name"]
                if entry.filename == prefix + ".dist-info/RECORD":
                    result["installer_mutable"].append(entry.filename)
                    continue
                if entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} or entry.file_size > 64 * 1024 * 1024:
                    raise ValueError("unsupported or oversized wheel member")
                with archive.open(entry) as member:
                    first = member.read(65536)
                    if entry.filename.startswith(prefix + ".data/scripts/") and first.startswith(b"#!python"):
                        result["unsupported"].append(entry.filename + " (installer shebang rewrite)")
                        continue
                    expected = hashlib.sha256(first)
                    for block in iter(lambda: member.read(1024 * 1024), b""):
                        expected.update(block)
                if not target.is_file():
                    result["issues"].append({"member": entry.filename, "reason": "MISSING"})
                    continue
                if target.stat().st_size != entry.file_size or inventory._hash(target).hex() != expected.hexdigest():
                    result["issues"].append({"member": entry.filename, "reason": "BYTES_DIFFER"})
                result["checked_files"] += 1
    return result


def compare(*, lock, wheelhouse, environment, output, apply=False):
    lock, wheelhouse, environment, output = map(Path, (lock, wheelhouse, environment, output))
    if not apply:
        return {"status": "PLAN_ONLY", "lock": str(lock), "wheelhouse": str(wheelhouse),
                "environment": str(environment), "output": str(output), "interpreter_started": False}
    if output.exists():
        raise ValueError("comparison output already exists; no overwrite")
    if lock.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("lock exceeds size limit")
    body = lock.read_bytes()
    manifest = json.loads(body)
    # Reuse the metadata-only preflight, preserving its separate, explicitly scoped evidence.
    preflight = inventory.inspect(lock=lock, environment=environment, output=output / "inventory", apply=True)
    preflight_bytes = (output / "inventory/inventory.json").read_bytes()
    preflight_report = json.loads(preflight_bytes)
    if preflight_report["lock_sha256"] != hashlib.sha256(body).hexdigest():
        raise ValueError("lock changed during preflight; partial evidence retained")
    environment, wheelhouse = environment.resolve(), wheelhouse.resolve()
    rows, owners = [], {}
    for row in manifest["wheels"]:
        try:
            rows.append(_package(row, wheelhouse, environment, owners))
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            rows.append({"name": row["name"], "checked_files": 0, "wheel_sha256_matches": False,
                         "unsupported": [], "issues": [{"reason": type(exc).__name__, "detail": str(exc)[:300]}]})
    failed = any(row["issues"] or not row["checked_files"] for row in rows)
    incomplete = any(row["unsupported"] for row in rows)
    matched = preflight["lock_versions_match"] and not failed and not incomplete
    status = ("ENVIRONMENT_MISMATCH" if not preflight["lock_versions_match"] else "WHEEL_COMPARISON_FAILED" if failed else
              "INCOMPLETE_MAPPING" if incomplete else "WHEEL_PAYLOADS_MATCH")
    report = {"schema": 1, "status": status, "at": datetime.now(timezone.utc).isoformat(),
        "environment": str(environment), "inventory_sha256": hashlib.sha256(preflight_bytes).hexdigest(),
        "lock_sha256": hashlib.sha256(body).hexdigest(), "packages": rows,
        "wheel_payloads_match": matched, "checked_files": sum(row["checked_files"] for row in rows),
        "bootstrap_packages_outside_lock": preflight["bootstrap_packages_outside_lock"],
        "installed_record_trusted": False, "interpreter_started": False, "network_performed": False,
        "untracked_files_checked": False, "generated_wrappers_verified": False, "security_verified": False}
    data = (json.dumps(report, ensure_ascii=True, indent=2) + "\n").encode()
    with (output / "wheel-comparison.json").open("xb") as stream:
        stream.write(data)
    with (output / "checksums.json").open("x", encoding="utf-8") as stream:
        json.dump({"wheel-comparison.json": hashlib.sha256(data).hexdigest()}, stream)
    return {"status": status, "wheel_payloads_match": matched, "checked_files": report["checked_files"],
            "package_count": len(rows), "security_verified": False, "report": str(output / "wheel-comparison.json")}
