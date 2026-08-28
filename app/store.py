from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

# One SQLite file, alongside the app rather than on a server. It fits how the rest of
# this project works — nothing leaves the machine holding the client's data — and a CA
# reconciling a few clients a month has no need for anything larger. The directory is
# gitignored: this file is the single largest concentration of client financial data
# in the project, holding every reconciliation, the uploaded statements themselves,
# and the workbooks built from them.
logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "reconciliation.db"


def _db_path() -> Path:
    return Path(os.environ.get("RECONCILIATION_DB", DEFAULT_DB_PATH))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at  TEXT NOT NULL,
    -- This engagement's Stage 3 thresholds, as the JSON an AnomalyConfig serialises
    -- to. NULL means the defaults. Stored per client because an approval limit is a
    -- property of the business, not of a run: a client whose limit is 25,00,000 gets
    -- nothing useful from flags calibrated to 50,000, and re-sending that with every
    -- upload is how it ends up wrong.
    anomaly_config TEXT
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
        _migrate(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()


def _migrate(connection: sqlite3.Connection) -> None:
    """Add columns to a database created by an earlier version.

    `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a
    database written before a column was added would never gain it and every read of
    that column would fail. Checked rather than attempted-and-caught so a genuine
    error still surfaces.
    """
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(clients)").fetchall()
    }
    if "anomaly_config" not in existing:
        connection.execute("ALTER TABLE clients ADD COLUMN anomaly_config TEXT")


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
            "SELECT id, name, created_at, anomaly_config FROM clients WHERE id = ?",
            (client_id,),
        ).fetchone()
    return dict(row) if row else None


def get_client_anomaly_config(client_id: str) -> Optional[dict[str, Any]]:
    """This client's stored Stage 3 thresholds, or None for the defaults."""
    with connect() as connection:
        row = connection.execute(
            "SELECT anomaly_config FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
    if row is None or row["anomaly_config"] is None:
        return None
    return json.loads(row["anomaly_config"])


def set_client_anomaly_config(
    client_id: str, config: Optional[dict[str, Any]]
) -> bool:
    """Store or clear this client's thresholds. None restores the defaults."""
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE clients SET anomaly_config = ? WHERE id = ?",
            (json.dumps(config) if config is not None else None, client_id),
        )
        return bool(cursor.rowcount)


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


# -------------------------------------------------------- export and backup


def export_client(client_id: str) -> Optional[bytes]:
    """Everything filed under one client, as a zip a person can read without this app.

    The database is otherwise the only copy of an engagement's history, and a format
    only this code can open is a poor place to leave a CA's working papers. Each run
    becomes a folder holding the workbook, the two source documents as uploaded, and
    the result as JSON, under a manifest naming what is inside.
    """
    import zipfile

    client = get_client(client_id)
    if client is None:
        return None

    runs = list_runs(client_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "client": {k: client[k] for k in ("id", "name", "created_at")},
                    "exported_at": _now(),
                    "runs": runs,
                },
                indent=2,
            ),
        )
        for run in runs:
            # Foldered by timestamp so the zip reads chronologically in a file
            # browser, which is how someone looks for "the July reconciliation".
            folder = f"{run['created_at'][:19].replace(':', '-')}_{run['id'][:8]}"
            stored = get_run(run["id"])
            if stored:
                archive.writestr(f"{folder}/result.json", json.dumps(stored["result"], indent=2))
            workbook = get_run_workbook(run["id"])
            if workbook:
                archive.writestr(f"{folder}/reconciliation_report.xlsx", workbook[1])
            for role, (name, content) in get_run_documents(run["id"]).items():
                archive.writestr(f"{folder}/source/{role}_{name}", content)

    return buffer.getvalue()


def backup_database() -> bytes:
    """A consistent copy of the whole database.

    Taken through SQLite's own backup API rather than by copying the file: a plain
    copy of a database being written to can be torn, and a backup that restores to a
    corrupt file is worse than none because nobody finds out until they need it.
    """
    import tempfile

    with connect() as source:
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            destination = sqlite3.connect(handle.name)
            try:
                source.backup(destination)
            finally:
                destination.close()
            return Path(handle.name).read_bytes()


# ---------------------------------------------------------------- retention


def purge_documents(older_than_days: int) -> dict[str, int]:
    """Drop the stored source files of runs older than a window, keeping the runs.

    The graceful half of retention. Documents are almost all of the stored bytes, and
    dropping them frees that while leaving the reconciliation itself, its figures and
    its file/page/line citations intact — a purged run loses the highlighted page
    image, not its audit trail. Deleting whole runs is `delete_run`, and is a
    different decision.
    """
    if older_than_days < 0:
        raise ValueError("A retention window cannot be negative.")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(
        timespec="microseconds"
    )
    with connect() as connection:
        runs = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM runs WHERE created_at < ?", (cutoff,)
            ).fetchall()
        ]
        if not runs:
            return {"runs_affected": 0, "documents_removed": 0}
        placeholders = ",".join("?" for _ in runs)
        cursor = connection.execute(
            f"DELETE FROM documents WHERE run_id IN ({placeholders})", runs
        )
        removed = cursor.rowcount
    logger.info(
        "Retention: removed %d stored document(s) from %d run(s) older than %d day(s)",
        removed, len(runs), older_than_days,
    )
    return {"runs_affected": len(runs), "documents_removed": removed}


def run_has_documents(run_id: str) -> bool:
    """Whether a run still has its source files, or has been purged.

    Worth surfacing: a run whose page highlights simply stopped appearing looks like
    a bug, where "the documents for this run have been purged" is an explanation.
    """
    with connect() as connection:
        return bool(
            connection.execute(
                "SELECT 1 FROM documents WHERE run_id = ? LIMIT 1", (run_id,)
            ).fetchone()
        )
