from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

# One SQLite file, alongside the app rather than on a server. It fits how the rest of
# this project works — nothing leaves the machine holding the client's data — and a CA
# reconciling a few clients a month has no need for anything larger. The directory is
# gitignored: this file is the single largest concentration of client financial data
# in the project, holding every reconciliation, the uploaded statements themselves,
# and the workbooks built from them.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "reconciliation.db"


def _db_path() -> Path:
    return Path(os.environ.get("RECONCILIATION_DB", DEFAULT_DB_PATH))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    client_id        TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    bank_file_name   TEXT NOT NULL,
    ledger_file_name TEXT NOT NULL,
    bank_template    TEXT,
    ledger_template  TEXT,
    -- The headline a reviewer picks a run by, kept separate from the full payload so
    -- listing a client's history never has to read (or parse) every stored result.
    summary_json     TEXT NOT NULL,
    -- The dashboard payload, minus the rendered page images: those are derivable from
    -- the stored documents and would otherwise be the largest thing in the row,
    -- duplicated for every run of the same statement.
    result_json      TEXT NOT NULL,
    workbook         BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS runs_by_client ON runs (client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS documents (
    id        TEXT PRIMARY KEY,
    run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    role      TEXT NOT NULL CHECK (role IN ('bank', 'ledger')),
    file_name TEXT NOT NULL,
    content   BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS documents_by_run ON documents (run_id);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the history database, creating it and its schema on first use.

    Foreign keys are enabled per connection — SQLite defaults them OFF, and without
    them deleting a client would silently orphan its runs and their documents rather
    than removing them, leaving client financial data behind after someone had
    explicitly asked for it to go.
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        yield connection
        connection.commit()
    finally:
        connection.close()


def _now() -> str:
    """Microsecond precision, deliberately.

    These timestamps order the client list and the history, and at second precision
    two clients added in the same second — or a run filed in the same second another
    client was created — tie, and the "most recently active first" ordering the
    sidebar depends on collapses into an arbitrary one.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ---------------------------------------------------------------- clients


class DuplicateClientError(ValueError):
    """Raised when a client name is already taken."""


def create_client(name: str) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("A client needs a name.")
    record = {"id": str(uuid.uuid4()), "name": name, "created_at": _now()}
    with connect() as connection:
        try:
            connection.execute(
                "INSERT INTO clients (id, name, created_at) VALUES (:id, :name, :created_at)",
                record,
            )
        except sqlite3.IntegrityError as exc:
            # Names are unique case-insensitively: two entries differing only in case
            # are the same firm to the person picking one from a list, and letting
            # both exist splits an engagement's history across them.
            raise DuplicateClientError(f"A client named {name!r} already exists.") from exc
    return {**record, "run_count": 0}


def list_clients() -> list[dict[str, Any]]:
    """Every client, most recently active first, with its run count.

    Ordered by last activity rather than name: the client someone is working on this
    week is the one they want at the top, and a CA carrying forty clients would
    otherwise scroll to reach it.
    """
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.name, c.created_at,
                   COUNT(r.id) AS run_count,
                   MAX(r.created_at) AS last_run_at
              FROM clients c
              LEFT JOIN runs r ON r.client_id = c.id
             GROUP BY c.id
             ORDER BY COALESCE(MAX(r.created_at), c.created_at) DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_client(client_id: str) -> Optional[dict[str, Any]]:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, created_at FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
    return dict(row) if row else None


def rename_client(client_id: str, name: str) -> Optional[dict[str, Any]]:
    name = name.strip()
    if not name:
        raise ValueError("A client needs a name.")
    with connect() as connection:
        try:
            cursor = connection.execute(
                "UPDATE clients SET name = ? WHERE id = ?", (name, client_id)
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateClientError(f"A client named {name!r} already exists.") from exc
        if not cursor.rowcount:
            return None
    return get_client(client_id)


def delete_client(client_id: str) -> bool:
    """Remove a client, its runs, and the documents stored with them.

    Deliberately a real delete rather than a flag. This holds client financial data,
    and someone removing a client is asking for it to be gone, not hidden.
    """
    with connect() as connection:
        cursor = connection.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        return bool(cursor.rowcount)


# ------------------------------------------------------------------- runs


def save_run(
    *,
    client_id: str,
    result: dict[str, Any],
    workbook: bytes,
    documents: dict[Literal["bank", "ledger"], tuple[str, bytes]],
    bank_template: str | None,
    ledger_template: str | None,
) -> dict[str, Any]:
    """Store one reconciliation against a client, with the files it was run on.

    The rendered page images are stripped before storing: they are reproducible from
    the documents kept alongside, and keeping both would make every run carry a
    second copy of its own statement.
    """
    payload = {key: value for key, value in result.items() if key != "page_images"}
    run_id = str(uuid.uuid4())
    bank_name, bank_bytes = documents["bank"]
    ledger_name, ledger_bytes = documents["ledger"]

    row = {
        "id": run_id,
        "client_id": client_id,
        "created_at": _now(),
        "bank_file_name": bank_name,
        "ledger_file_name": ledger_name,
        "bank_template": bank_template,
        "ledger_template": ledger_template,
        "summary_json": json.dumps(_headline(result)),
        "result_json": json.dumps(payload),
        "workbook": workbook,
    }

    with connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM clients WHERE id = ?", (client_id,)
        ).fetchone():
            raise LookupError(f"No client with id {client_id!r}")
        connection.execute(
            """
            INSERT INTO runs (id, client_id, created_at, bank_file_name, ledger_file_name,
                              bank_template, ledger_template, summary_json, result_json, workbook)
            VALUES (:id, :client_id, :created_at, :bank_file_name, :ledger_file_name,
                    :bank_template, :ledger_template, :summary_json, :result_json, :workbook)
            """,
            row,
        )
        connection.executemany(
            "INSERT INTO documents (id, run_id, role, file_name, content) VALUES (?, ?, ?, ?, ?)",
            [
                (str(uuid.uuid4()), run_id, "bank", bank_name, bank_bytes),
                (str(uuid.uuid4()), run_id, "ledger", ledger_name, ledger_bytes),
            ],
        )
    return {"id": run_id, "created_at": row["created_at"]}


def _headline(result: dict[str, Any]) -> dict[str, Any]:
    """The few figures a reviewer scans a history list by."""
    summary = result.get("summary", {})
    return {
        "matched": len(result.get("matched", [])),
        "ai_matched": len(result.get("ai_matched", [])),
        "unmatched_bank": len(result.get("unmatched_bank", [])),
        "unmatched_ledger": len(result.get("unmatched_ledger", [])),
        "anomalies": len(result.get("anomalies", [])),
        "difference": summary.get("difference"),
        "unexplained": summary.get("unexplained"),
    }


def list_runs(client_id: str) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, created_at, bank_file_name, ledger_file_name, summary_json
              FROM runs WHERE client_id = ? ORDER BY created_at DESC
            """,
            (client_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "bank_file_name": row["bank_file_name"],
            "ledger_file_name": row["ledger_file_name"],
            "summary": json.loads(row["summary_json"]),
        }
        for row in rows
    ]


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    """The stored dashboard payload for one run, without its page images."""
    with connect() as connection:
        row = connection.execute(
            """
            SELECT r.id, r.client_id, r.created_at, r.bank_file_name, r.ledger_file_name,
                   r.bank_template, r.ledger_template, r.result_json, c.name AS client_name
              FROM runs r JOIN clients c ON c.id = r.client_id
             WHERE r.id = ?
            """,
            (run_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "client_name": row["client_name"],
        "created_at": row["created_at"],
        "bank_file_name": row["bank_file_name"],
        "ledger_file_name": row["ledger_file_name"],
        "result": json.loads(row["result_json"]),
    }


def get_run_documents(run_id: str) -> dict[str, tuple[str, bytes]]:
    """The files a run was produced from, keyed by role.

    Kept so a run opened months later still supports the same click-through to the
    source as it did on the day: without the document there is no page to highlight,
    and the citation drops back to naming a line in a file nobody can open.
    """
    with connect() as connection:
        rows = connection.execute(
            "SELECT role, file_name, content FROM documents WHERE run_id = ?", (run_id,)
        ).fetchall()
    return {row["role"]: (row["file_name"], bytes(row["content"])) for row in rows}


def get_run_workbook(run_id: str) -> Optional[tuple[str, bytes]]:
    with connect() as connection:
        row = connection.execute(
            "SELECT bank_file_name, workbook FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return (row["bank_file_name"], bytes(row["workbook"])) if row else None


def delete_run(run_id: str) -> bool:
    with connect() as connection:
        return bool(connection.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount)
