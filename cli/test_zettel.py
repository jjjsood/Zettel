"""End-to-End-Test der CLI gegen eine frische DB aus den App-Migrationen (ohne Sidecar/PDF)."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
MIGRATIONS = ROOT.parent / "src" / "lib" / "db" / "migrations"


@pytest.fixture
def zettel(tmp_path):
    data = tmp_path / "digital.laux.zettel"
    data.mkdir()
    con = sqlite3.connect(data / "zettel.db")
    for f in sorted(MIGRATIONS.glob("*.sql")):
        con.executescript(f.read_text(encoding="utf-8"))
    con.execute("UPDATE settings SET is_kleinunternehmer = 0 WHERE id = 1")  # Default der Migrationen: Kleinunternehmer
    con.commit()
    con.close()
    env = {**os.environ, "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}

    def run(*args, ok=True):
        p = subprocess.run([sys.executable, str(ROOT / "zettel.py"), "--json", *args],
                           capture_output=True, text=True, env=env)
        out = json.loads(p.stdout)
        assert out["ok"] is ok, p.stdout
        assert (p.returncode == 0) is ok
        return out

    run.db = data / "zettel.db"
    return run


ITEMS = "Beratung|2|Std|90|19"


def test_full_flow_and_idempotency(zettel):
    c = zettel("customer:create", "--name", "ACME", "--city", "Berlin", "--idempotency-key", "c1")
    assert c["customer"]["customerNumber"] == "K-0001" and not c["reused"]
    again = zettel("customer:create", "--name", "ACME", "--idempotency-key", "c1")
    assert again["reused"] and again["customer"]["id"] == c["customer"]["id"]
    assert len(zettel("customers")["customers"]) == 1

    inv = zettel("invoice:create", "--customer", "K-0001", "--items", ITEMS, "--idempotency-key", "i1", "--issue")["invoice"]
    assert inv["totalCent"] == 21420 and inv["number"].startswith("RE-") and inv["status"] == "sent"
    assert zettel("invoice:create", "--customer", "K-0001", "--items", ITEMS, "--idempotency-key", "i1")["reused"]
    assert len(zettel("invoices")["invoices"]) == 1
    # kein Key -> neuer Entwurf, ohne Nummer
    draft = zettel("invoice:create", "--customer", "ACME", "--items", ITEMS)["invoice"]
    assert draft["number"] is None and draft["status"] == "draft"

    # Mahnung nur für überfällige 'sent'-Rechnung; Key -> keine zweite Nummer verbrannt
    r = zettel("reminder:create", "--invoice", str(inv["id"]), "--level", "2", "--idempotency-key", "r1")
    assert r["reminder"]["number"].startswith("MA-") and r["reminder"]["feeCent"] == 500
    assert zettel("reminder:create", "--invoice", str(inv["id"]), "--level", "2", "--idempotency-key", "r1")["reused"]
    assert len(zettel("reminders")["reminders"]) == 1
    assert zettel("reminder:send", str(r["reminder"]["id"]))["changed"]
    assert not zettel("reminder:send", str(r["reminder"]["id"]))["changed"]
    zettel("reminder:create", "--invoice", str(draft["id"]), "--level", "1", ok=False)

    # Angebot -> senden (idempotent) -> annehmen -> umwandeln (idempotent)
    o = zettel("offer:create", "--customer", "ACME", "--items", ITEMS, "--idempotency-key", "o1")["offer"]
    assert o["number"].startswith("AN-") and o["totalCent"] == 21420
    assert zettel("offer:create", "--customer", "ACME", "--items", ITEMS, "--idempotency-key", "o1")["reused"]
    assert zettel("offer:send", str(o["id"]))["changed"] and not zettel("offer:send", str(o["id"]))["changed"]
    zettel("offer:reject", str(o["id"]), ok=True)
    zettel("offer:send", str(o["id"]), ok=False)  # rejected -> sent nicht erlaubt
    o2 = zettel("offer:create", "--customer", "ACME", "--items", ITEMS)["offer"]
    conv = zettel("offer:convert", str(o2["id"]))
    assert conv["invoice"]["status"] == "draft" and not conv["reused"]
    assert zettel("offer:convert", str(o2["id"]))["reused"]
    assert zettel("offer:show", str(o2["id"]))["offer"]["status"] == "accepted"

    # bezahlt ist idempotent, überfällig-Filter, Stats
    zettel("invoice:paid", str(inv["id"]))
    zettel("invoice:paid", str(inv["id"]))
    con = sqlite3.connect(zettel.db)
    assert con.execute("SELECT COUNT(*) FROM invoice_payments").fetchone()[0] == 1
    assert zettel("invoices", "--status", "paid")["invoices"][0]["id"] == inv["id"]
    assert zettel("stats")["revenueCent"] == 21420


def test_errors_are_json(zettel):
    e = zettel("invoice:show", "99", ok=False)
    assert "nicht gefunden" in e["error"]
    assert "Kunde nicht gefunden" in zettel("invoice:create", "--customer", "nix", "--items", ITEMS, ok=False)["error"]
