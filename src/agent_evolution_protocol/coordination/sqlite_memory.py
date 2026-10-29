"""Local durable memory for agent conversations and coordination evidence.

SQLite is deliberately the required backend: it is bundled with Python and
keeps the protocol portable. FTS5 is used when available and gracefully
degrades to LIKE queries on constrained SQLite builds.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  event_id TEXT UNIQUE NOT NULL,
  agent TEXT NOT NULL,
  role TEXT NOT NULL,
  workspace TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_agent_time ON events(agent, created_at);
CREATE INDEX IF NOT EXISTS idx_events_session_time ON events(session_id, created_at);
"""


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.fts_enabled = self._ensure_fts()

    def _ensure_fts(self) -> bool:
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(event_id, agent, text, content='events', content_rowid='id')")
            self.db.executescript("""
            CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
              INSERT INTO events_fts(rowid,event_id,agent,text) VALUES (new.id,new.event_id,new.agent,new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
              INSERT INTO events_fts(events_fts,rowid,event_id,agent,text) VALUES('delete',old.id,old.event_id,old.agent,old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
              INSERT INTO events_fts(events_fts,rowid,event_id,agent,text) VALUES('delete',old.id,old.event_id,old.agent,old.text);
              INSERT INTO events_fts(rowid,event_id,agent,text) VALUES (new.id,new.event_id,new.agent,new.text);
            END;
            """)
            self.db.commit()
            return True
        except sqlite3.OperationalError:
            return False

    def append(self, *, event_id: str, agent: str, role: str, text: str,
               workspace: str = "", session_id: str = "",
               metadata: dict[str, Any] | None = None) -> bool:
        self.db.execute("""INSERT OR IGNORE INTO events
          (event_id,agent,role,workspace,session_id,text,metadata_json,created_at)
          VALUES (?,?,?,?,?,?,?,?)""", (event_id, agent, role, workspace,
          session_id, text, json.dumps(metadata or {}, ensure_ascii=False),
          datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        return True

    def search(self, query: str, *, agent: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if self.fts_enabled:
            rows = self.db.execute("""SELECT e.* FROM events_fts f JOIN events e ON e.id=f.rowid
              WHERE events_fts MATCH ? AND (? IS NULL OR e.agent=?)
              ORDER BY e.created_at DESC LIMIT ?""", (query, agent, agent, limit)).fetchall()
            # The default FTS tokenizer is intentionally conservative for
            # CJK text. Preserve reliable substring recall as a fallback.
            if not rows:
                rows = self.db.execute("""SELECT * FROM events WHERE text LIKE ?
                  AND (? IS NULL OR agent=?) ORDER BY created_at DESC LIMIT ?""",
                  (f"%{query}%", agent, agent, limit)).fetchall()
        else:
            rows = self.db.execute("""SELECT * FROM events WHERE text LIKE ?
              AND (? IS NULL OR agent=?) ORDER BY created_at DESC LIMIT ?""",
              (f"%{query}%", agent, agent, limit)).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.db.close()


def open_memory(coord_root: Path) -> MemoryStore:
    return MemoryStore(Path(coord_root) / "memory.sqlite3")
