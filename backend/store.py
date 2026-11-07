"""Small SQLite store. Every write group is an immediate transaction."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def decode(value):
    return json.loads(value)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL, canonical TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checklists (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE, content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    checklist_version TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, content TEXT NOT NULL, dialogue TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                );
                CREATE INDEX IF NOT EXISTS evaluation_history
                    ON evaluations(conversation_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS active_evaluation
                    ON evaluations(conversation_id, checklist_version)
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS appeals (
                    id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL,
                    criterion_id TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, content TEXT NOT NULL,
                    FOREIGN KEY(evaluation_id) REFERENCES evaluations(id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS open_appeal
                    ON appeals(evaluation_id, criterion_id) WHERE status = 'open';
            """)

    @contextmanager
    def transaction(self, *, write=True):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def update_evaluation(db, document):
        # Appeals live in their own journal and are attached when reading.
        payload = {**document, "appeals": []}
        db.execute("UPDATE evaluations SET status=?, content=? WHERE id=?",
                   (document["status"], encode(payload), document["evaluation_id"]))

    def recover(self):
        with self.transaction() as db:
            rows = db.execute("SELECT content FROM evaluations WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                doc = decode(row["content"])
                doc["status"] = "failed"
                doc["error"] = {"code": "SERVER_RESTARTED", "message":
                                "Сервер перезапущен до завершения оценки. Запустите новую оценку явно.",
                                "details": None}
                self.update_evaluation(db, doc)

    @staticmethod
    def evaluation(db, evaluation_id):
        row = db.execute("SELECT content FROM evaluations WHERE id=?", (evaluation_id,)).fetchone()
        if row is None:
            return None
        document = decode(row["content"])
        document["appeals"] = [decode(r["content"]) for r in db.execute(
            "SELECT content FROM appeals WHERE evaluation_id=? ORDER BY created_at, rowid", (evaluation_id,))]
        return document
