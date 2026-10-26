"""Durable per-request checkpoints and atomic report publication."""

import contextlib
import errno
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextlib.contextmanager
def lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".lock").open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if isinstance(error, BlockingIOError) or os.name == "nt" or error.errno in (errno.EACCES, errno.EAGAIN):
                raise ValueError("Another synchronization or rebuild is already running") from None
            raise
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.directory / "source.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS responses (
                run_id TEXT NOT NULL REFERENCES runs(id), path TEXT NOT NULL,
                kind TEXT NOT NULL, context TEXT NOT NULL,
                fetched_at TEXT NOT NULL, body TEXT, error TEXT, status_code INTEGER,
                PRIMARY KEY (run_id, path)
            );
            CREATE INDEX IF NOT EXISTS responses_kind ON responses(kind);
            CREATE TABLE IF NOT EXISTS tasks (
                run_id TEXT NOT NULL REFERENCES runs(id), path TEXT NOT NULL,
                kind TEXT NOT NULL, context TEXT NOT NULL,
                PRIMARY KEY (run_id, path)
            );
        """)

    def close(self):
        self.db.close()

    def check_account(self, account_id):
        row = self.db.execute("SELECT value FROM metadata WHERE key='account_id'").fetchone()
        if row and row[0] != account_id:
            raise ValueError("This data directory belongs to another ECNPlus account; choose a different --data-dir")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('account_id', ?)", (account_id,))
        self.db.commit()

    def start(self, resume=False):
        if resume:
            row = self.db.execute("SELECT id, status FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
            if not row or row['status'] == 'complete':
                raise ValueError("The latest sync is already complete or no sync exists; run sync without --resume")
            run_id = row['id']
            self.db.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=?", (run_id,))
        else:
            run_id = identifier()
            self.db.execute("INSERT INTO runs VALUES (?, ?, NULL, 'running')", (run_id, now()))
        self.db.commit()
        return run_id

    def finish(self, run_id, status):
        self.db.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?", (status, now(), run_id))
        self.db.commit()

    def save(self, run_id, task, body=None, error=None, status_code=None):
        self.db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            run_id, task['path'], task['kind'], json.dumps(task['context']), now(),
            json.dumps(body, ensure_ascii=False, allow_nan=False) if body is not None else None,
            error, status_code,
        ))
        self.db.commit()

    def enqueue(self, run_id, tasks):
        self.db.executemany("INSERT OR IGNORE INTO tasks VALUES (?, ?, ?, ?)", (
            (run_id, task['path'], task['kind'], json.dumps(task['context'])) for task in tasks
        ))
        self.db.commit()

    def tasks(self, run_id):
        rows = self.db.execute("""
            SELECT tasks.*, responses.fetched_at, responses.error, responses.status_code,
                   responses.body IS NOT NULL AND responses.error IS NULL AS success
            FROM tasks LEFT JOIN responses USING (run_id, path)
            WHERE tasks.run_id=? ORDER BY kind, path
        """, (run_id,))
        return [{**dict(row), 'context': json.loads(row['context'])} for row in rows]

    def response(self, run_id, path):
        row = self.db.execute("SELECT * FROM responses WHERE run_id=? AND path=? AND error IS NULL", (run_id, path)).fetchone()
        return json.loads(row['body']) if row else None

    def responses(self, run_id=None):
        query = "SELECT responses.* FROM responses JOIN runs ON runs.id=run_id"
        parameters = ()
        if run_id:
            query += " WHERE run_id=?"
            parameters = (run_id,)
        query += " ORDER BY runs.started_at, responses.fetched_at, path"
        for row in self.db.execute(query, parameters):
            item = dict(row)
            item['context'] = json.loads(item['context'])
            item['body'] = json.loads(item['body']) if item['body'] is not None else None
            yield item

    def latest_run(self):
        row = self.db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def run(self, run_id):
        return dict(self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
