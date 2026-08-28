from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app

client = TestClient(app)

_BANK_CSV = (
    b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
    b"01/04/24,NEFT VENDOR PAYMENT ABC,N123,15075.00,\n"
)
_LEDGER_CSV = (
    b"Txn Date,Particulars,Voucher No,Amount\n"
    b"01-04-2024,Vendor Payment - ABC,N123,-15075\n"
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own database file.

    The real one holds client financial data; a test suite must never open it, let
    alone write to it.
    """
    monkeypatch.setenv("RECONCILIATION_DB", str(tmp_path / "history.db"))
    yield


def _reconcile(client_id: str | None = None):
    data = {"client_id": client_id} if client_id else {}
    return client.post(
        "/reconcile/preview",
        files={
            "bank_file": ("bank.csv", _BANK_CSV, "text/csv"),
            "ledger_file": ("ledger.csv", _LEDGER_CSV, "text/csv"),
        },
        data=data,
    )


def test_client_names_are_unique_regardless_of_case() -> None:
    """Two entries differing only in case are the same firm to whoever picks one from
    a list, and letting both exist splits an engagement's history across them."""
    assert client.post("/clients", data={"name": "Orion Retail Pvt Ltd"}).status_code == 201
    duplicate = client.post("/clients", data={"name": "orion retail pvt ltd"})
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_a_run_is_filed_against_its_client_and_can_be_reopened() -> None:
    """The point of the history: a reconciliation is worth little as a one-shot view
    that vanishes on refresh, since a CA returns to an engagement weeks later."""
    created = client.post("/clients", data={"name": "Orion Retail"}).json()

    live = _reconcile(created["id"])
    assert live.status_code == 200
    assert live.json()["saved_run"]["id"]

    runs = client.get(f"/clients/{created['id']}/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["bank_file_name"] == "bank.csv"
    assert runs[0]["summary"]["matched"] == len(live.json()["matched"])

    reopened = client.get(f"/runs/{runs[0]['id']}").json()
    assert reopened["client_name"] == "Orion Retail"
    # The stored result is the same reconciliation, not a re-run of it.
    assert len(reopened["result"]["matched"]) == len(live.json()["matched"])
    assert reopened["result"]["summary"] == live.json()["summary"]


def test_reopened_run_keeps_its_source_citations() -> None:
    """A run whose citations stop working the moment it is filed would make the
    history a worse view of the same work. The documents are stored with the run so
    a reviewer coming back months later gets the same click-through to the source."""
    created = client.post("/clients", data={"name": "Nova Textiles"}).json()
    run_id = _reconcile(created["id"]).json()["saved_run"]["id"]

    result = client.get(f"/runs/{run_id}").json()["result"]
    rows = [p["bank"] for p in result["matched"]] + [p["ledger"] for p in result["matched"]]
    assert rows
    for row in rows:
        assert row["source_ref"]["text"], "the source line must survive storage"
        assert row["source_ref"]["label"]


def test_a_run_without_a_client_is_shown_but_not_kept() -> None:
    """Choosing a client is not required to reconcile. Refusing to run without one
    would put filing in front of the work."""
    created = client.post("/clients", data={"name": "Apex Traders"}).json()

    body = _reconcile(None).json()
    assert body.get("saved_run") is None
    assert client.get(f"/clients/{created['id']}/runs").json()["runs"] == []


def test_reconciling_against_an_unknown_client_is_rejected() -> None:
    """Silently dropping the filing would leave a CA believing a run was kept."""
    assert _reconcile("not-a-real-client-id").status_code == 404


def test_deleting_a_client_takes_its_runs_and_documents_with_it() -> None:
    """SQLite leaves foreign keys off by default, so without the pragma this would
    orphan the runs and the stored statements — client financial data left behind
    after someone explicitly asked for it to go."""
    created = client.post("/clients", data={"name": "Zenith Foods"}).json()
    run_id = _reconcile(created["id"]).json()["saved_run"]["id"]

    assert client.delete(f"/clients/{created['id']}").status_code == 200
    assert client.get(f"/runs/{run_id}").status_code == 404

    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_history_lists_most_recently_active_client_first() -> None:
    """A CA carrying forty clients wants the one they are working on at the top, not
    an alphabetical list they have to scroll."""
    first = client.post("/clients", data={"name": "Alpha Co"}).json()
    second = client.post("/clients", data={"name": "Beta Co"}).json()
    _reconcile(first["id"])

    names = [c["name"] for c in client.get("/clients").json()["clients"]]
    assert names[0] == "Alpha Co", names
    assert client.get("/clients").json()["clients"][0]["run_count"] == 1


def test_stored_workbook_is_downloadable_and_matches_the_run() -> None:
    """The workbook is stored rather than rebuilt, so a download from history is the
    same file the reviewer saw on the day — a rebuild could differ."""
    created = client.post("/clients", data={"name": "Globex"}).json()
    live = _reconcile(created["id"])
    run_id = live.json()["saved_run"]["id"]

    response = client.get(f"/runs/{run_id}/report.xlsx")
    assert response.status_code == 200
    sheets = pd.read_excel(io.BytesIO(response.content), sheet_name=None)
    assert len(sheets["Matched"]) == len(live.json()["matched"])


def test_renaming_a_client_keeps_its_history() -> None:
    created = client.post("/clients", data={"name": "Old Name Ltd"}).json()
    _reconcile(created["id"])

    renamed = client.patch(f"/clients/{created['id']}", data={"name": "New Name Ltd"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "New Name Ltd"
    assert len(client.get(f"/clients/{created['id']}/runs").json()["runs"]) == 1


def test_client_thresholds_apply_to_that_clients_runs() -> None:
    """An approval limit is a property of the business, not of a run. Re-sending it
    with every upload is how it ends up wrong on the one run nobody checked."""
    created = client.post("/clients", data={"name": "Apex Ltd"}).json()

    # 15,075 sits under nobody's default threshold.
    before = _reconcile(created["id"]).json()
    assert not [f for f in before["anomalies"] if f["rule"] == "just_below_approval_threshold"]

    body = {"config": json.dumps({"approval_thresholds": ["16000"], "threshold_margin_pct": "0.10"})}
    assert client.put(f"/clients/{created['id']}/anomaly-config", data=body).status_code == 200

    after = _reconcile(created["id"]).json()
    assert [f for f in after["anomalies"] if f["rule"] == "just_below_approval_threshold"]


def test_a_request_config_overrides_the_clients_stored_one() -> None:
    """Overriding rather than merging: merging would leave a caller unable to ask for
    plain defaults on one run, and a half-applied threshold set is harder to reason
    about than either whole one."""
    created = client.post("/clients", data={"name": "Override Co"}).json()
    client.put(
        f"/clients/{created['id']}/anomaly-config",
        data={"config": json.dumps({"approval_thresholds": ["16000"], "threshold_margin_pct": "0.10"})},
    )

    response = client.post(
        "/reconcile/preview",
        files={
            "bank_file": ("bank.csv", _BANK_CSV, "text/csv"),
            "ledger_file": ("ledger.csv", _LEDGER_CSV, "text/csv"),
        },
        data={"client_id": created["id"], "anomaly_config": json.dumps({"approval_thresholds": []})},
    )
    flags = [f for f in response.json()["anomalies"] if f["rule"] == "just_below_approval_threshold"]
    assert flags == []


def test_thresholds_can_be_cleared_back_to_the_defaults() -> None:
    created = client.post("/clients", data={"name": "Reset Co"}).json()
    client.put(
        f"/clients/{created['id']}/anomaly-config",
        data={"config": json.dumps({"approval_thresholds": ["16000"]})},
    )
    assert client.get(f"/clients/{created['id']}/anomaly-config").json()["is_default"] is False

    client.put(f"/clients/{created['id']}/anomaly-config", data={})
    assert client.get(f"/clients/{created['id']}/anomaly-config").json()["is_default"] is True


def test_export_contains_every_run_with_its_workbook_and_sources() -> None:
    """The database is otherwise the only copy of an engagement's history, and a
    format only this app can open is a poor place to leave a CA's working papers."""
    import zipfile

    created = client.post("/clients", data={"name": "Export Co"}).json()
    _reconcile(created["id"])

    response = client.get(f"/clients/{created['id']}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["client"]["name"] == "Export Co"
        assert len(manifest["runs"]) == 1
        assert any(n.endswith("reconciliation_report.xlsx") for n in names), names
        assert any("/source/bank_bank.csv" in n for n in names), names
        assert any("/source/ledger_ledger.csv" in n for n in names), names
        assert any(n.endswith("result.json") for n in names), names


def test_backup_restores_to_a_readable_database(tmp_path) -> None:
    """A plain file copy of a database being written to can be torn, and a backup
    that restores to a corrupt file is worse than none because nobody finds out
    until they need it."""
    created = client.post("/clients", data={"name": "Backup Co"}).json()
    _reconcile(created["id"])

    response = client.get("/backup")
    assert response.status_code == 200

    restored = tmp_path / "restored.db"
    restored.write_bytes(response.content)

    import sqlite3
    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        names = [r[0] for r in connection.execute("SELECT name FROM clients")]
        assert "Backup Co" in names
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        connection.close()


def test_purging_documents_keeps_the_reconciliation_and_its_citations() -> None:
    """The graceful half of retention. Documents are almost all of the stored bytes;
    dropping them frees that while leaving each run's figures and its file/page/line
    citations intact. A purged run loses its page image, not its audit trail."""
    created = client.post("/clients", data={"name": "Retention Co"}).json()
    run_id = _reconcile(created["id"]).json()["saved_run"]["id"]

    # Nothing is old enough yet, so a 30-day window must not touch it.
    assert store.purge_documents(30) == {"runs_affected": 0, "documents_removed": 0}
    assert client.get(f"/runs/{run_id}").json()["documents_available"] is True

    purged = client.post("/retention/purge-documents", data={"older_than_days": 0})
    assert purged.status_code == 200
    assert purged.json()["documents_removed"] == 2

    reopened = client.get(f"/runs/{run_id}").json()
    assert reopened["documents_available"] is False
    assert reopened["result"]["page_images"] == {}
    # The run itself, and every citation on it, survives.
    rows = [p["bank"] for p in reopened["result"]["matched"]]
    assert rows and all(r["source_ref"]["text"] for r in rows)


def test_a_negative_retention_window_is_rejected() -> None:
    assert client.post("/retention/purge-documents", data={"older_than_days": -1}).status_code == 422
