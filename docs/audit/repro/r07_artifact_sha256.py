"""R-07: the snapshot's ``artifacts[].sha256`` is not the file's SHA-256.  It is
the SHA-256 of a JSON envelope wrapping the hex encoding of the bytes, so any
independent verification (``sha256sum``, ``hashlib.file_digest``, a second
implementation of the same schema) disagrees with the recorded value and reads
as tampering.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r07_artifact_sha256.py
"""
import hashlib
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.federation import snapshot

folder = Path(tempfile.mkdtemp(prefix="aep-r07-"))
path = folder / "report.md"
path.write_bytes(b"hello artifact\n")

pinned = snapshot.artifact(path)
real = hashlib.sha256(path.read_bytes()).hexdigest()
print("snapshot.artifact().sha256 =", pinned["sha256"])
print("sha256 of the file bytes   =", real)
print("equal                      =", pinned["sha256"] == real)
print("pinned                     =", json.dumps(pinned, ensure_ascii=False))

# Same content, different name -> the recorded value is content-derived, so the
# field is *a* digest; it is simply not the digest its name promises.
copy = folder / "report-copy.md"
copy.write_bytes(b"hello artifact\n")
print("same bytes, other name     =", snapshot.artifact(copy)["sha256"] == pinned["sha256"])

# What an auditor following the schema would conclude:
print("verdict a verifier reaches =", "TAMPERED" if pinned["sha256"] != real else "ok")
print("folder =", folder)
