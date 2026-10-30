"""Persistent Federation Workspace Engine: workspace owns the task, agents do not."""
from .adapter import Adapter, FakeAdapter, find, match
from .diagnosis import classify, decide
from .evolution import inherit, load, new_library, promote, propose, record, retire, save
from .handoff import accept, packet, verify as verify_handoff
from .snapshot import build, make_anchor, rollback, save as save_snapshot, verify as verify_snapshot
from .store import EventLog, atomic_write, digest, read_json

__all__ = [
    "Adapter", "FakeAdapter", "find", "match",
    "classify", "decide",
    "new_library", "propose", "record", "promote", "retire", "inherit", "save", "load",
    "packet", "accept", "verify_handoff",
    "make_anchor", "build", "verify_snapshot", "save_snapshot", "rollback",
    "EventLog", "atomic_write", "digest", "read_json",
]
