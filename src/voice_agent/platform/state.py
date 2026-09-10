import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["speak", "call_tool", "ask_user", "wait", "complete", "fail"]
    message: str = Field(default="", max_length=3000)
    plan: list[str] = Field(default_factory=list, max_length=8)
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check(self):
        if self.action == "call_tool" and not self.tool:
            raise ValueError("tool is required")
        if self.action != "call_tool" and (self.tool or self.arguments):
            raise ValueError("Only call_tool may contain tool arguments")
        if self.action != "call_tool" and not self.message.strip():
            raise ValueError("message is required")
        if any(not p.strip() or len(p) > 300 for p in self.plan):
            raise ValueError("Plan entries must contain 1–300 characters")
        return self


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "task_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        self.db.commit()

    def create(self, goal, numbers):
        task = {
            "id": uuid.uuid4().hex,
            "goal": goal,
            "status": "queued",
            "plan": [],
            "created_at": time.time(),
            "updated_at": time.time(),
            "steps": 0,
            "messages": [{"role": "user", "content": goal}],
            "allowed_numbers": numbers,
            "pending": None,
            "executions": [],
            "result": "",
        }
        self.save(task, "created", {"goal": goal})
        return task

    def save(self, task, event, payload=None):
        task["updated_at"] = time.time()
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO tasks VALUES (?,?)",
                (task["id"], json.dumps(task, ensure_ascii=False)),
            )
            self.db.execute(
                "INSERT INTO events(task_id,data) VALUES (?,?)",
                (
                    task["id"],
                    json.dumps(
                        {"type": event, "time": time.time(), "payload": payload or {}},
                        ensure_ascii=False,
                    ),
                ),
            )

    def get(self, task_id):
        with self.lock:
            row = self.db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return json.loads(row[0])

    def list(self):
        with self.lock:
            rows = self.db.execute("SELECT data FROM tasks ORDER BY rowid DESC").fetchall()
        return [json.loads(r[0]) for r in rows]

    def events(self, task_id, after=0):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,data FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 200",
                (task_id, after),
            ).fetchall()
        return [{"id": r[0], **json.loads(r[1])} for r in rows]

    def recover(self):
        for task in self.list():
            if task["status"] in {"running", "queued"}:
                task["status"] = "paused"
                for execution in task["executions"]:
                    if execution["status"] == "running":
                        execution["status"] = "unknown"
                self.save(task, "recovered", {"message": "服务重启，未自动重放操作"})

    def close(self):
        self.db.close()
