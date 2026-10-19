"""SQLite store: versioned requirements library + audit log.

Every change creates a new version row; nothing is overwritten, so any
requirement's history (and who changed it, and why) can be reconstructed.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .model import Decision, Requirement, Status

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "reqtrace.db"
SEED = ROOT / "data" / "requirements.json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requirements (
    id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (id, version));
CREATE TABLE IF NOT EXISTS audit (
    ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    obj TEXT NOT NULL, detail TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(_SCHEMA)

    # -- seeding -------------------------------------------------------------
    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM requirements").fetchone()[0] == 0

    def load_seed(self, seed: str | Path = SEED) -> int:
        items = json.loads(Path(seed).read_text(encoding="utf-8"))
        for raw in items:
            req = Requirement.model_validate(raw)
            self._insert(req)
        self.log(Decision(actor="system", action="seed", obj="library",
                          detail={"count": len(items), "source": Path(seed).name}))
        return len(items)

    # -- reads ---------------------------------------------------------------
    def current(self, include_retired: bool = False) -> list[Requirement]:
        rows = self.db.execute(
            "SELECT body FROM requirements r WHERE version = "
            "(SELECT MAX(version) FROM requirements WHERE id = r.id) ORDER BY id").fetchall()
        reqs = [Requirement.model_validate_json(b) for (b,) in rows]
        if not include_retired:
            reqs = [r for r in reqs if r.status != Status.RETIRED]
        return reqs

    def get(self, req_id: str) -> Requirement | None:
        row = self.db.execute(
            "SELECT body FROM requirements WHERE id = ? ORDER BY version DESC LIMIT 1",
            (req_id,)).fetchone()
        return Requirement.model_validate_json(row[0]) if row else None

    def history(self, req_id: str) -> list[Requirement]:
        rows = self.db.execute(
            "SELECT body FROM requirements WHERE id = ? ORDER BY version", (req_id,)).fetchall()
        return [Requirement.model_validate_json(b) for (b,) in rows]

    def audit(self, obj_prefix: str = "") -> list[Decision]:
        rows = self.db.execute(
            "SELECT ts, actor, action, obj, detail FROM audit WHERE obj LIKE ? ORDER BY ts",
            (obj_prefix + "%",)).fetchall()
        return [Decision(ts=t, actor=a, action=ac, obj=o, detail=json.loads(d))
                for t, a, ac, o, d in rows]

    # -- writes --------------------------------------------------------------
    def new_version(self, req_id: str, changes: dict, actor: str, reason: str) -> Requirement:
        """Apply field changes as a new version; the old one is kept."""
        old = self.get(req_id)
        if old is None:
            raise KeyError(req_id)
        data = old.model_dump()
        data.update(changes)
        data["version"] = old.version + 1
        new = Requirement.model_validate(data)
        self._insert(new)
        self.log(Decision(actor=actor, action="new_version", obj=f"req:{req_id}",
                          detail={"from": old.version, "to": new.version,
                                  "changes": {k: str(v) for k, v in changes.items()},
                                  "reason": reason}))
        return new

    def log(self, d: Decision) -> None:
        self.db.execute("INSERT INTO audit VALUES (?, ?, ?, ?, ?)",
                        (d.ts.isoformat(), d.actor, d.action, d.obj,
                         json.dumps(d.detail, ensure_ascii=False)))
        self.db.commit()

    def _insert(self, req: Requirement) -> None:
        self.db.execute("INSERT INTO requirements VALUES (?, ?, ?)",
                        (req.id, req.version, req.model_dump_json()))
        self.db.commit()


def open_store(path: str | Path = DEFAULT_DB) -> Store:
    """Open the store, seeding it from data/requirements.json on first use."""
    s = Store(path)
    if s.is_empty():
        s.load_seed()
    return s
