"""Portable SQLite knowledge store for cross-agent sessions and lessons."""
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, task_id TEXT, actor TEXT, title TEXT, body TEXT NOT NULL, tags TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_records_task ON records(task_id);
CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind);
"""

def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path)); db.executescript(SCHEMA); db.commit(); return db

def add(path: Path, *, kind: str, body: str, task_id: str = "", actor: str = "", title: str = "", tags: str = "") -> int:
    if kind not in {"intent", "session", "decision", "dissent", "evidence", "failure", "lesson", "code_detail"}: raise ValueError("invalid record kind")
    if not body.strip(): raise ValueError("body must not be empty")
    db = connect(path)
    try:
        cur = db.execute("INSERT INTO records(kind,task_id,actor,title,body,tags) VALUES(?,?,?,?,?,?)", (kind, task_id, actor, title, body, tags)); db.commit(); return int(cur.lastrowid)
    finally: db.close()

def search(path: Path, query: str = "", *, kind: str = "", task_id: str = "", limit: int = 50) -> list[dict]:
    db = connect(path)
    try:
        sql = "SELECT id,kind,task_id,actor,title,body,tags,created_at FROM records WHERE 1=1"; params = []
        if query: sql += " AND (body LIKE ? OR title LIKE ? OR tags LIKE ?)"; params += [f"%{query}%"] * 3
        if kind: sql += " AND kind = ?"; params.append(kind)
        if task_id: sql += " AND task_id = ?"; params.append(task_id)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        return [dict(row) for row in db.execute(sql, params).fetchall()]
    finally: db.close()
