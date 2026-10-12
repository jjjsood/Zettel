#!/usr/bin/env python3
"""
Zettel CLI — headless Steuerung der Zettel-Datenbank.

Repliziert die App-Geschäftslogik aus src/lib/db/{invoices,offers,reminders,queries}.ts exakt:
  - Draft-Placeholder `DRAFT-<hex>` (kein Nummern-Slot wird verbrannt)
  - Lazy Invoice-Numbering: echte Nummer erst bei issue/markSent/PDF
  - computeTotals: Per-Line-Rundung (Finanzamt-Konvention), Cent-Integer
  - markPaid protokolliert Restbetrag als invoice_payments-Eintrag
  - PDF-Generierung über den Sidecar (JSON-RPC über stdin/stdout)

DB-Auswahl folgt derselben Quelle der Wahrheit wie die App:
  sandbox.flag vorhanden  -> zettel-sandbox.db
  sonst aktiver Tenant    -> dessen Pfad aus tenants.json
  sonst                   -> zettel.db (Standard)

Nur stdlib — bewusst ohne externe Abhängigkeiten.

Warnung: Nicht parallel zur laufenden Zettel-App mit schreibendem Zugriff
verwenden (SQLite single-writer). Lesende Befehle sind unproblematisch.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

APP_IDENTIFIER = "digital.laux.zettel"

# ----------------------------------------------------------------------------
# DB-Auflösung (Spiegel von src-tauri/src/tenants.rs)
# ----------------------------------------------------------------------------


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_IDENTIFIER


def resolve_db_path() -> Path:
    """Aktive DB: Sandbox > aktiver Tenant > Standard. Wie resolve_active_url()."""
    d = app_data_dir()
    if (d / "sandbox.flag").exists():
        return d / "zettel-sandbox.db"
    cfg_path = d / "tenants.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            active = cfg.get("active")
            if active and active != "default":
                for t in cfg.get("tenants", []):
                    if t.get("id") == active and str(t.get("path", "")).strip():
                        return Path(t["path"])
        except (json.JSONDecodeError, OSError):
            pass
    return d / "zettel.db"


def connect() -> sqlite3.Connection:
    path = resolve_db_path()
    if not path.exists():
        sys.exit(f"Fehler: Datenbank nicht gefunden: {path}")
    con = sqlite3.connect(path, timeout=10)  # busy_timeout: die App kann parallel schreiben
    con.row_factory = sqlite3.Row
    return con


# ----------------------------------------------------------------------------
# Geschäftslogik (Spiegel von src/lib/utils/totals.ts + invoices.ts)
# ----------------------------------------------------------------------------


def js_round(v: float) -> int:
    """Math.round wie in JS: half away from zero (Pythons round() ist banker's)."""
    return int(math.floor(v + 0.5)) if v >= 0 else int(math.ceil(v - 0.5))


def compute_line_total(quantity: float, unit_price_cent: int) -> int:
    return js_round(quantity * unit_price_cent)


def compute_totals(items: list[dict], is_kleinunternehmer: bool, is_reverse_charge: bool) -> dict:
    vat_exempt = is_kleinunternehmer or is_reverse_charge
    subtotal = 0
    vat_amount = 0
    for it in items:
        line = compute_line_total(it["quantity"], it["unitPrice"])
        subtotal += line
        if not vat_exempt:
            vat_amount += js_round(line * it["vatRate"] / 100)
    return {"subtotal": subtotal, "vatAmount": vat_amount, "total": subtotal + vat_amount}


def format_invoice_number(pattern: str, counter: int, dt: datetime) -> str:
    yyyy = str(dt.year)
    yy = yyyy[2:]
    mm = str(dt.month).zfill(2)
    return (
        pattern.replace("{YYYY}", yyyy)
        .replace("{YY}", yy)
        .replace("{MM}", mm)
        .replace("{NNNN}", str(counter).zfill(4))
        .replace("{NNN}", str(counter).zfill(3))
        .replace("{NN}", str(counter).zfill(2))
        .replace("{N}", str(counter))
    )


def draft_placeholder() -> str:
    return f"DRAFT-{uuid.uuid4().hex[:16]}"


def is_draft_number(n: str | None) -> bool:
    return bool(n) and n.startswith("DRAFT-")


def display_number(number: str, status: str) -> str:
    if status == "draft" and is_draft_number(number):
        return f"Entwurf #{number[6:10]}"
    return number


def now_unix() -> int:
    return int(time.time())


def parse_date(s: str | None) -> int | None:
    """Akzeptiert YYYY-MM-DD / TT.MM.JJJJ (lokal, 12:00 Uhr) oder Unix-Timestamp."""
    if not s:
        return None
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return int(datetime(dt.year, dt.month, dt.day, 12, 0, 0).timestamp())
        except ValueError:
            continue
    sys.exit(f"Ungültiges Datum: {s} (erwartet YYYY-MM-DD oder TT.MM.JJJJ)")


def fmt_eur(cent) -> str:
    if cent is None:
        return "—"
    return f"{cent / 100:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_date(unix) -> str:
    if not unix:
        return "—"
    return datetime.fromtimestamp(unix).strftime("%d.%m.%Y")


# ----------------------------------------------------------------------------
# Sidecar-Integration (JSON-RPC wie src-tauri/src/sidecar.rs)
# ----------------------------------------------------------------------------


SIDECAR_SEARCH_PATHS = [
    # installierte App (NSIS user-scope) — der Normalfall
    Path.home() / "AppData" / "Local" / "Zettel" / "sidecar" / "zettel-sidecar.exe",
    Path.home() / "AppData" / "Local" / "Programs" / "Zettel" / "sidecar" / "zettel-sidecar.exe",
    Path.home() / "AppData" / "Local" / "zettel" / "sidecar" / "zettel-sidecar.exe",
    # Dev-Repo-Venv (Fallback)
    Path(__file__).resolve().parent.parent / "sidecar" / ".venv" / "Scripts" / "python.exe",
    Path(__file__).resolve().parent.parent / "sidecar" / ".venv" / "bin" / "python",
]


def resolve_sidecar() -> tuple[str | None, str | None]:
    """(exe, None) für ein Standalone-Binary (bevorzugt: installierte App)
    oder (python, main.py) für das Dev-Venv im Repo."""
    env = os.environ.get("ZETTEL_SIDECAR")
    if env:
        p = Path(env)
        if p.is_file():
            if p.suffix.lower() == ".exe":
                return str(p), None
            return str(p), str(_find_main_py())
    repo_root = Path(__file__).resolve().parent.parent
    main_py = repo_root / "sidecar" / "main.py"
    for cand in SIDECAR_SEARCH_PATHS:
        if not cand.is_file():
            continue
        if cand.suffix.lower() == ".exe":
            return str(cand), None
        # Dev-Venv braucht main.py daneben
        if main_py.is_file():
            return str(cand), str(main_py)
    return None, None


def _find_main_py() -> str:
    repo_root = Path(__file__).resolve().parent.parent
    main_py = repo_root / "sidecar" / "main.py"
    if not main_py.is_file():
        sys.exit("sidecar/main.py nicht gefunden (Repo-Pfad?).")
    return str(main_py)


def call_sidecar(command: str, payload: dict) -> dict:
    exe, main_py = resolve_sidecar()
    if not exe:
        sys.exit(
            "Kein Sidecar verfügbar. Dev: sidecar/.venv anlegen. "
            "Release: ZETTEL_SIDECAR auf zettel-sidecar.exe setzen."
        )
    cmd = [exe] if main_py is None else [exe, main_py]
    req = json.dumps({"command": command, "payload": payload})
    proc = subprocess.run(
        cmd,
        input=req,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(Path(main_py).parent) if main_py else None,
    )
    if proc.returncode != 0:
        sys.exit(f"Sidecar-Fehler (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        sys.exit(f"Sidecar-Antwort nicht parsebar:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")


# ----------------------------------------------------------------------------
# PDF-Payload-Bau (Spiegel von src/lib/sidecar/invoice.ts buildPayload)
# ----------------------------------------------------------------------------


def skonto_of(inv: dict) -> dict | None:
    if inv.get("skonto_percent") is None or inv.get("skonto_days") is None:
        return None
    total = abs(inv["total"])
    return {
        "percent": inv["skonto_percent"],
        "days": inv["skonto_days"],
        "discountCent": js_round(total * inv["skonto_percent"] / 100),
        "deadlineUnix": (inv["issue_date"] or now_unix()) + inv["skonto_days"] * 86400,
    }


def build_pdf_payload(inv: dict, items: list[dict], customer: dict, company: dict, output_path: str) -> dict:
    skonto = skonto_of(inv)
    return {
        "invoice": {
            "number": inv["number"],
            "issueDate": inv["issue_date"],
            "deliveryDate": inv["delivery_date"],
            "dueDate": inv["due_date"],
            "subtotal": inv["subtotal"],
            "vatAmount": inv["vat_amount"],
            "total": inv["total"],
            "isKleinunternehmer": bool(inv["is_kleinunternehmer"]),
            "isReverseCharge": bool(inv["is_reverse_charge"]),
            "reverseChargeType": inv["reverse_charge_type"],
            "isCreditNote": bool(inv["is_credit_note"]),
            "correctsInvoice": None,
            "notes": inv["notes"],
            "paymentTerms": inv["payment_terms"],
            "currency": inv["currency"] or "EUR",
            "exchangeRate": inv["exchange_rate"],
            "eurTotalCent": inv["eur_total_cent"],
            "servicePeriodStart": inv["service_period_start"],
            "servicePeriodEnd": inv["service_period_end"],
            "skontoPercent": skonto["percent"] if skonto else None,
            "skontoDays": skonto["days"] if skonto else None,
            "skontoAmountCent": skonto["discountCent"] if skonto else None,
            "skontoDeadline": skonto["deadlineUnix"] if skonto else None,
            "amountPaidCent": inv["amount_paid_cent"] or 0,
            "pdfLanguage": inv["pdf_language"] or "de",
        },
        "items": [
            {
                "position": it["position"],
                "description": it["description"],
                "quantity": it["quantity"],
                "unit": it["unit"],
                "unitPrice": it["unit_price"],
                "vatRate": it["vat_rate"],
                "lineTotal": it["line_total"],
                "longDescription": it["long_description"],
                "linePeriodStart": it["line_period_start"],
                "linePeriodEnd": it["line_period_end"],
            }
            for it in items
        ],
        "company": {
            "companyName": company["company_name"],
            "ownerName": company["owner_name"],
            "street": company["street"],
            "postalCode": company["postal_code"],
            "city": company["city"],
            "country": company["country"],
            "taxNumber": company["tax_number"],
            "vatId": company["vat_id"],
            "email": company["email"],
            "phone": company["phone"],
            "website": company["website"],
            "bankName": company["bank_name"],
            "bankIban": company["bank_iban"],
            "bankBic": company["bank_bic"],
            "isKleinunternehmer": bool(company["is_kleinunternehmer"]),
            "kleinunternehmerNote": company["kleinunternehmer_note"],
            "logoPath": company["logo_path"],
        },
        "customer": {
            "name": customer.get("name"),
            "contactPerson": customer.get("contactPerson") or customer.get("contact_person"),
            "street": customer.get("street"),
            "postalCode": customer.get("postalCode") or customer.get("postal_code"),
            "city": customer.get("city"),
            "country": customer.get("country"),
            "vatId": customer.get("vatId") or customer.get("vat_id"),
        },
        "outputPath": output_path,
        "profile": company["zugferd_profile"] or "en16931",
        "settings": {"pdf_theme": company["pdf_theme"] or "classic"},
    }


# ----------------------------------------------------------------------------
# Ausgabe (--json) & Idempotenz
# ----------------------------------------------------------------------------

JSON_MODE = False


def emit(data: dict, text: str = "") -> None:
    """Text für Menschen, mit --json ein einzelnes JSON-Objekt {"ok": true, ...}."""
    if JSON_MODE:
        print(json.dumps({"ok": True, **data}, ensure_ascii=False))
    else:
        print(text)


def iso(unix) -> str | None:
    return datetime.fromtimestamp(unix).strftime("%Y-%m-%d") if unix else None


def eur_to_cent(s) -> int:
    try:
        return js_round(float(str(s).replace(",", ".")) * 100)
    except ValueError:
        sys.exit(f"Ungültiger Betrag: {s}")


def idem_file() -> Path:
    return app_data_dir() / "cli-idempotency.json"


def _idem_load() -> dict:
    try:
        return json.loads(idem_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _idem_id(kind: str, key: str) -> str:
    return f"{resolve_db_path()}|{kind}|{key}"


def idem_lookup(con, kind: str, key: str | None, table: str) -> int | None:
    """id des bereits mit diesem Key angelegten Datensatzes (nur wenn er noch existiert)."""
    if not key:
        return None
    rid = _idem_load().get(_idem_id(kind, key))
    if rid and con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (rid,)).fetchone():
        return rid
    return None


def idem_store(kind: str, key: str | None, row_id: int) -> None:
    # ponytail: Key liegt neben der DB, nicht darin (keine Migration). Stirbt der Prozess zwischen
    # COMMIT und Speichern, entsteht beim Retry ein Duplikat. Echte Garantie = Spalte + Migration.
    if not key:
        return
    data = _idem_load()
    data[_idem_id(kind, key)] = row_id
    f = idem_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(f)


def create_once(con, kind: str, table: str, key: str | None, create) -> tuple[int, bool]:
    """(id, reused). Mit Key wird bei Wiederholung nichts neu angelegt."""
    existing = idem_lookup(con, kind, key, table)
    if existing:
        return existing, True
    rid = create()
    idem_store(kind, key, rid)
    return rid, False


def reuse_note(reused: bool, key: str | None) -> str:
    return f" (idempotency-key '{key}': bereits vorhanden, nichts neu angelegt)" if reused else ""


def snap_of(raw) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ----------------------------------------------------------------------------
# Kunden
# ----------------------------------------------------------------------------


def resolve_customer(con, ref: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM customers WHERE id = ? OR customer_number = ? OR name = ? COLLATE NOCASE",
        (int(ref) if ref.isdigit() else -1, ref, ref),
    ).fetchone()
    if not row:
        sys.exit(f"Kunde nicht gefunden: {ref} (siehe 'zettel.py customers')")
    return row


def customer_snapshot(customer) -> dict:
    return {
        "customerNumber": customer["customer_number"],
        "name": customer["name"],
        "contactPerson": customer["contact_person"],
        "street": customer["street"],
        "postalCode": customer["postal_code"],
        "city": customer["city"],
        "country": customer["country"],
        "email": customer["email"],
        "vatId": customer["vat_id"],
    }


def customer_dict(r) -> dict:
    return {
        "id": r["id"], "customerNumber": r["customer_number"], "name": r["name"],
        "contactPerson": r["contact_person"], "street": r["street"], "postalCode": r["postal_code"],
        "city": r["city"], "country": r["country"], "email": r["email"], "phone": r["phone"],
        "vatId": r["vat_id"],
    }


def next_customer_number(con) -> str:
    """K-0001 = MAX(numerischer Suffix)+1, Spiegel von nextCustomerNumber() in queries.ts."""
    mx = 0
    for (n,) in con.execute("SELECT customer_number FROM customers WHERE customer_number LIKE 'K-%'"):
        try:
            mx = max(mx, int(n[2:]))
        except ValueError:
            pass
    return f"K-{mx + 1:04d}"


def cmd_status(_args) -> None:
    path = resolve_db_path()
    con = connect()
    n_inv = con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    n_cust = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    sandbox = (app_data_dir() / "sandbox.flag").exists()
    exe, _ = resolve_sidecar()
    emit(
        {"db": str(path), "sandbox": sandbox, "invoices": n_inv, "customers": n_cust, "sidecar": exe},
        f"DB:         {path}\nSandbox:    {'JA' if sandbox else 'nein'}\n"
        f"Rechnungen: {n_inv}  ·  Kunden: {n_cust}\nSidecar:    {exe or 'nicht verfügbar'}",
    )
    con.close()


def cmd_customers(_args) -> None:
    con = connect()
    rows = con.execute("SELECT * FROM customers ORDER BY id").fetchall()
    text = "\n".join(
        f"{c['id']:>3}  {c['customer_number']:<8} {c['name']:<40} {c['city'] or '':<20} {c['email'] or ''}"
        for c in rows
    ) or "Keine Kunden."
    emit({"customers": [customer_dict(c) for c in rows]}, text)
    con.close()


def cmd_customer_create(args) -> None:
    con = connect()

    def create() -> int:
        cur = con.execute(
            """INSERT INTO customers (customer_number, name, contact_person, street, postal_code, city, country,
               email, phone, vat_id, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (args.number or next_customer_number(con), args.name, args.contact_person, args.street or "",
             args.postal_code or "", args.city or "", args.country or "DE", args.email, args.phone, args.vat_id,
             args.notes),
        )
        con.commit()
        return cur.lastrowid

    cid, reused = create_once(con, "customer", "customers", args.idempotency_key, create)
    c = con.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    emit(
        {"customer": customer_dict(c), "reused": reused},
        f"Kunde {'vorhanden' if reused else 'angelegt'}: id={cid}  {c['customer_number']}  {c['name']}"
        + reuse_note(reused, args.idempotency_key),
    )
    con.close()


# ----------------------------------------------------------------------------
# Rechnungen
# ----------------------------------------------------------------------------


def inv_dict(r) -> dict:
    return {
        "id": r["id"],
        "number": None if is_draft_number(r["number"]) else r["number"],
        "displayNumber": display_number(r["number"], r["status"]),
        "status": r["status"],
        "customer": snap_of(r["customer_snapshot"]).get("name"),
        "issueDate": iso(r["issue_date"]),
        "dueDate": iso(r["due_date"]),
        "totalCent": r["total"],
        "currency": r["currency"] or "EUR",
        "pdfPath": r["pdf_path"],
    }


def cmd_invoice_list(args) -> None:
    con = connect()
    q = "SELECT * FROM invoices"
    cond, params = [], []
    if args.status:
        cond.append("status = ?")
        params.append(args.status)
    if args.year:
        cond.append("CAST(strftime('%Y', issue_date, 'unixepoch') AS INT) = ?")
        params.append(args.year)
    if args.overdue:
        cond.append("status IN ('sent','partial') AND due_date < ? AND COALESCE(is_credit_note,0) = 0")
        params.append(now_unix())
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY id DESC"
    rows = con.execute(q, params).fetchall()
    levels: dict[int, list[int]] = {}
    for rr in con.execute("SELECT invoice_id, level FROM reminders ORDER BY level"):
        levels.setdefault(rr["invoice_id"], []).append(rr["level"])
    text = "\n".join(
        f"{r['id']:>3}  {display_number(r['number'], r['status']):<15} "
        f"{fmt_date(r['issue_date'])}  {fmt_eur(r['total']):>12}  {r['status']:<9} "
        f"{'PDF' if r['pdf_path'] else '—':<3} fällig {fmt_date(r['due_date'])}"
        for r in rows
    )
    emit({"invoices": [{**inv_dict(r), "reminderLevels": levels.get(r["id"], [])} for r in rows]}, text)
    con.close()


def load_invoice_full(con, invoice_id: int) -> tuple[dict, list[dict], dict]:
    inv = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        sys.exit(f"Rechnung {invoice_id} nicht gefunden.")
    items = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY position", (invoice_id,)
        ).fetchall()
    ]
    return dict(inv), items, snap_of(inv["customer_snapshot"])


def issue_invoice(con, invoice_id: int, sent_at: int | None = None) -> str:
    """Spiegel von issueInvoice(): DRAFT -> echte Nummer, status sent."""
    inv = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        sys.exit(f"Rechnung {invoice_id} nicht gefunden.")
    if inv["status"] != "draft":
        return inv["number"]
    ts = sent_at or now_unix()
    final_number = inv["number"]
    if is_draft_number(inv["number"]):
        final_number = next_number(con, "invoice")
    con.execute(
        "UPDATE invoices SET number = ?, status = 'sent', sent_at = ?, updated_at = unixepoch() "
        "WHERE id = ? AND status = 'draft'",
        (final_number, ts, invoice_id),
    )
    con.commit()
    return final_number


def next_number(con, kind: str) -> str:
    """Zähler +1 und Format anwenden (invoice/offer/reminder), Spiegel von next*Number() in der App."""
    con.execute(
        f"UPDATE settings SET {kind}_number_counter = {kind}_number_counter + 1, updated_at = unixepoch() WHERE id = 1"
    )
    row = con.execute(
        f"SELECT {kind}_number_counter c, {kind}_number_format f FROM settings WHERE id = 1"
    ).fetchone()
    return format_invoice_number(row["f"], row["c"], datetime.now())


def safe_filename(s: str) -> str:
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in s)


def archive_pdf_version(path: Path) -> str | None:
    """Spiegel von fs_export.rs archive_pdf_version: bestehendes PDF vor dem
    Überschreiben nach <dir>/Versionen/<stem>__<mtime>.pdf verschieben."""
    if not path.is_file():
        return None
    versions_dir = path.parent / "Versionen"
    versions_dir.mkdir(parents=True, exist_ok=True)
    mtime = int(path.stat().st_mtime)
    dest = versions_dir / f"{path.stem}__{mtime}.pdf"
    n = 2
    while dest.exists():
        dest = versions_dir / f"{path.stem}__{mtime}-{n}.pdf"
        n += 1
    path.rename(dest)
    return str(dest)


PDF_DIRS = {"invoice": "Rechnungen", "offer": "Angebote", "reminder": "Mahnungen"}
PDF_TABLES = {"invoice": "invoices", "offer": "offers", "reminder": "reminders"}


def pdf_output_dir(kind: str = "invoice") -> Path:
    """PDF-Zielordner. In der Sandbox wird bewusst getrennt:
    ~/Documents/Zettel-Sandbox/<Ordner> — Sandbox-PDFs dürfen produktive
    PDFs nie überschreiben (Nummernkreise sind pro DB unabhängig!)."""
    root = "Zettel-Sandbox" if resolve_db_path().name == "zettel-sandbox.db" else "Zettel"
    return Path.home() / "Documents" / root / PDF_DIRS[kind]


def build_offer_payload(offer: dict, items: list[dict], customer: dict, company: dict, output_path: str) -> dict:
    """Spiegel von src/lib/sidecar/offer.ts buildPayload (Items/Firma/Kunde wie bei der Rechnung)."""
    skonto = skonto_of(offer)
    base = build_pdf_payload(
        {**offer, "delivery_date": None, "due_date": None, "reverse_charge_type": None, "payment_terms": None,
         "is_credit_note": 0, "currency": None, "exchange_rate": None, "eur_total_cent": None,
         "amount_paid_cent": 0},
        items, customer, company, output_path,
    )
    return {
        "offer": {
            "number": offer["number"],
            "issueDate": offer["issue_date"],
            "validUntil": offer["valid_until"],
            "subtotal": offer["subtotal"],
            "vatAmount": offer["vat_amount"],
            "total": offer["total"],
            "isKleinunternehmer": bool(offer["is_kleinunternehmer"]),
            "isReverseCharge": bool(offer["is_reverse_charge"]),
            "notes": offer["notes"],
            "introText": offer["intro_text"],
            "servicePeriodStart": offer["service_period_start"],
            "servicePeriodEnd": offer["service_period_end"],
            "skontoPercent": skonto["percent"] if skonto else None,
            "skontoDays": skonto["days"] if skonto else None,
            "skontoAmountCent": skonto["discountCent"] if skonto else None,
            "skontoDeadline": skonto["deadlineUnix"] if skonto else None,
            "pdfLanguage": offer["pdf_language"] or "de",
        },
        "items": base["items"],
        "company": {k: v for k, v in base["company"].items() if k not in ("bankName", "bankIban", "bankBic")},
        "customer": base["customer"],
        "outputPath": output_path,
        "settings": base["settings"],
    }


REMINDER_LABELS = {1: "Zahlungserinnerung", 2: "Mahnung", 3: "Letzte Mahnung"}


def build_reminder_payload(rem: dict, company: dict, output_path: str) -> dict:
    """Spiegel von src/lib/sidecar/reminder.ts."""
    snap = snap_of(rem["invoice_snapshot"])
    return {
        "reminder": {
            "number": rem["number"],
            "level": rem["level"],
            "levelLabel": REMINDER_LABELS[rem["level"]],
            "issueDate": rem["issue_date"],
            "dueDate": rem["due_date"],
            "feeCents": rem["fee_cents"],
            "interestCents": rem["interest_cents"],
            "originalTotalCents": rem["original_total_cents"],
            "totalDueCents": rem["total_due_cents"],
            "bodyText": rem["body_text"],
        },
        "invoice": {
            "invoiceNumber": snap.get("invoiceNumber"),
            "invoiceIssueDate": snap.get("invoiceIssueDate"),
            "invoiceDueDate": snap.get("invoiceDueDate"),
        },
        "customer": {
            "name": snap.get("customerName"),
            "contactPerson": snap.get("customerContactPerson"),
            "street": snap.get("customerStreet"),
            "postalCode": snap.get("customerPostalCode"),
            "city": snap.get("customerCity"),
            "country": snap.get("customerCountry"),
        },
        "company": {
            "companyName": company["company_name"],
            "ownerName": company["owner_name"],
            "street": company["street"],
            "postalCode": company["postal_code"],
            "city": company["city"],
            "country": company["country"],
            "taxNumber": company["tax_number"],
            "vatId": company["vat_id"],
            "email": company["email"],
            "phone": company["phone"],
            "website": company["website"],
            "bankName": company["bank_name"],
            "bankIban": company["bank_iban"],
            "bankBic": company["bank_bic"],
            "logoPath": company["logo_path"],
        },
        "outputPath": output_path,
        "settings": {"pdf_theme": company["pdf_theme"] or "classic"},
    }


def generate_pdf(con, kind: str, row_id: int) -> tuple[str, str]:
    """PDF für Rechnung/Angebot/Mahnung über den Sidecar; Rückgabe (Nummer, PDF-Pfad)."""
    table = PDF_TABLES[kind]
    if not con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone():
        sys.exit(f"{kind} {row_id} nicht gefunden.")
    if kind == "invoice":
        inv = con.execute("SELECT status, number FROM invoices WHERE id = ?", (row_id,)).fetchone()
        if inv["status"] == "draft" and is_draft_number(inv["number"]):
            issue_invoice(con, row_id)
    row = dict(con.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())
    company = dict(con.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    out_dir = pdf_output_dir(kind)
    output_path = out_dir / f"{safe_filename(row['number'])}.pdf"
    if kind == "reminder":
        payload = build_reminder_payload(row, company, str(output_path))
    else:
        items = [
            dict(r)
            for r in con.execute(
                f"SELECT * FROM {kind}_items WHERE {kind}_id = ? ORDER BY position", (row_id,)
            ).fetchall()
        ]
        build = build_pdf_payload if kind == "invoice" else build_offer_payload
        payload = build(row, items, snap_of(row["customer_snapshot"]), company, str(output_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_pdf_version(output_path)
    resp = call_sidecar(f"generate_{kind}", payload)
    if not resp.get("success"):
        err = resp.get("error", {})
        sys.exit(f"PDF-Fehler [{err.get('code')}]: {err.get('message')}\n{err.get('details', '')[:800]}")
    con.execute(f"UPDATE {table} SET pdf_path = ?, updated_at = unixepoch() WHERE id = ?", (resp["pdfPath"], row_id))
    con.commit()
    return row["number"], resp["pdfPath"]


def parse_items_arg(spec: str | None) -> list[dict]:
    """Format: 'Beschreibung|Menge|Einheit|Einzelpreis-Euro[|MwSt%][|Langtext]'.
    Mehrere Positionen mit ; trennen."""
    if not spec:
        return []
    items = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        fields = part.split("|")
        if len(fields) < 4:
            sys.exit(f"Position ungültig (min. 4 Felder): {part}")
        desc = fields[0].strip()
        try:
            qty = float(fields[1].strip().replace(",", "."))
        except ValueError:
            sys.exit(f"Menge ungültig in Position: {part}")
        unit = fields[2].strip() or "Std"
        price_cent = eur_to_cent(fields[3].strip())
        try:
            vat = float(fields[4].strip().replace(",", ".")) if len(fields) > 4 and fields[4].strip() else 0
        except ValueError:
            sys.exit(f"MwSt ungültig in Position: {part}")
        long_desc = fields[5].strip() if len(fields) > 5 and fields[5].strip() else None
        items.append(
            {
                "description": desc,
                "quantity": qty,
                "unit": unit,
                "unitPrice": price_cent,
                "vatRate": int(vat),
                "longDescription": long_desc,
            }
        )
    return items


def create_invoice(
    con, customer: str, items: list[dict], *, issue_date=None, due_date=None, delivery_date=None,
    service_start=None, service_end=None, payment_terms: int | None = None, reverse_charge: str = "none",
    skonto_percent=None, skonto_days=None, pdf_language: str = "de", notes=None,
) -> int:
    """Draft (mit DRAFT-Platzhalter) anlegen, Rückgabe: id."""
    settings = dict(con.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    cust = resolve_customer(con, str(customer))
    if not items:
        sys.exit("Keine Positionen angegeben (siehe --items).")
    is_rc = reverse_charge != "none"
    totals = compute_totals(items, bool(settings["is_kleinunternehmer"]), is_rc)
    issue_ts = parse_date(issue_date) or now_unix()
    terms_days = payment_terms if payment_terms is not None else (settings["default_payment_terms_days"] or 14)
    due_ts = parse_date(due_date) or (issue_ts + terms_days * 86400)
    cur = con.execute(
        """INSERT INTO invoices
           (number, customer_id, customer_snapshot, issue_date, delivery_date, due_date,
            status, subtotal, vat_amount, total, is_kleinunternehmer, is_reverse_charge,
            reverse_charge_type, notes, payment_terms, is_credit_note, corrects_invoice_id,
            currency, exchange_rate, eur_total_cent, notes_internal, follow_up_date,
            service_period_start, service_period_end, skonto_percent, skonto_days, pdf_language)
           VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL,
                   'EUR', NULL, ?, NULL, NULL, ?, ?, ?, ?, ?)""",
        (
            draft_placeholder(),
            cust["id"],
            json.dumps(customer_snapshot(cust), ensure_ascii=False),
            issue_ts,
            parse_date(delivery_date),
            due_ts,
            totals["subtotal"],
            totals["vatAmount"],
            totals["total"],
            1 if settings["is_kleinunternehmer"] else 0,
            1 if is_rc else 0,
            reverse_charge,
            notes,
            f"Zahlbar innerhalb von {terms_days} Tagen ohne Abzug.",
            totals["total"],
            parse_date(service_start),
            parse_date(service_end),
            skonto_percent,
            skonto_days,
            pdf_language,
        ),
    )
    invoice_id = cur.lastrowid
    for i, it in enumerate(items, start=1):
        con.execute(
            """INSERT INTO invoice_items
               (invoice_id, position, description, quantity, unit, unit_price, vat_rate, line_total,
                long_description, line_period_start, line_period_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invoice_id,
                i,
                it["description"],
                it["quantity"],
                it["unit"],
                it["unitPrice"],
                it["vatRate"],
                compute_line_total(it["quantity"], it["unitPrice"]),
                it.get("longDescription"),
                it.get("linePeriodStart"),
                it.get("linePeriodEnd"),
            ),
        )
    con.commit()
    return invoice_id


def items_dict(rows) -> list[dict]:
    return [
        {"position": r["position"], "description": r["description"], "quantity": r["quantity"], "unit": r["unit"],
         "unitPriceCent": r["unit_price"], "vatRate": r["vat_rate"], "lineTotalCent": r["line_total"]}
        for r in rows
    ]


def items_text(rows) -> str:
    return "\n".join(
        f"  {r['position']}. {r['description']}\n"
        f"     {r['quantity']:g} {r['unit']} × {fmt_eur(r['unit_price'])} = {fmt_eur(r['line_total'])}"
        for r in rows
    )


def cmd_invoice_create(args) -> None:
    con = connect()
    items = parse_items_arg(args.items)
    iid, reused = create_once(
        con, "invoice", "invoices", args.idempotency_key,
        lambda: create_invoice(
            con, args.customer, items,
            issue_date=args.issue_date, due_date=args.due_date, delivery_date=args.delivery_date,
            service_start=args.service_start, service_end=args.service_end, payment_terms=args.payment_terms,
            reverse_charge=args.reverse_charge, skonto_percent=args.skonto_percent,
            skonto_days=args.skonto_days, pdf_language=args.pdf_language, notes=args.notes,
        ),
    )
    lines = [f"Draft {'vorhanden' if reused else 'erstellt'}: id={iid}" + reuse_note(reused, args.idempotency_key)]
    if args.issue or args.pdf:
        lines.append(f"Bereitgestellt: {issue_invoice(con, iid)}")
    if args.pdf:
        if reused and con.execute("SELECT pdf_path FROM invoices WHERE id = ?", (iid,)).fetchone()[0]:
            lines.append("PDF bereits vorhanden — nicht neu erzeugt.")
        else:
            lines.append(f"PDF: {generate_pdf(con, 'invoice', iid)[1]}")
    inv = con.execute("SELECT * FROM invoices WHERE id = ?", (iid,)).fetchone()
    lines.append(f"Gesamt: {fmt_eur(inv['total'])}  Kunde: {inv_dict(inv)['customer']}")
    emit({"invoice": inv_dict(inv), "reused": reused}, "\n".join(lines))
    con.close()


def cmd_invoice_show(args) -> None:
    con = connect()
    inv, items, cust = load_invoice_full(con, args.id)
    lines = [
        f"Rechnung {display_number(inv['number'], inv['status'])}  [{inv['status']}]",
        f"Kunde:        {cust.get('name')} ({cust.get('customerNumber')})",
        f"Datum:        {fmt_date(inv['issue_date'])}  ·  fällig {fmt_date(inv['due_date'])}",
    ]
    if inv["service_period_start"]:
        lines.append(f"Leistung:     {fmt_date(inv['service_period_start'])} – {fmt_date(inv['service_period_end'])}")
    lines += [f"Zahlungsziel: {inv['payment_terms'] or '—'}", "", items_text(items), "",
              f"Netto: {fmt_eur(inv['subtotal'])}  ·  USt: {fmt_eur(inv['vat_amount'])}  ·  Gesamt: {fmt_eur(inv['total'])}"]
    if inv["pdf_path"]:
        lines.append(f"PDF:   {inv['pdf_path']}")
    emit(
        {"invoice": {**inv_dict(inv), "subtotalCent": inv["subtotal"], "vatAmountCent": inv["vat_amount"],
                     "amountPaidCent": inv["amount_paid_cent"] or 0, "paymentTerms": inv["payment_terms"],
                     "servicePeriodStart": iso(inv["service_period_start"]),
                     "servicePeriodEnd": iso(inv["service_period_end"]), "items": items_dict(items)}},
        "\n".join(lines),
    )
    con.close()


def cmd_invoice_pdf(args) -> None:
    con = connect()
    number, path = generate_pdf(con, "invoice", args.id)
    emit({"number": number, "pdfPath": path}, f"{number}: {path}")
    con.close()


def cmd_invoice_issue(args) -> None:
    con = connect()
    number = issue_invoice(con, args.id, parse_date(args.sent_at))
    emit({"id": args.id, "number": number}, f"Bereitgestellt: {number}")
    con.close()


def cmd_invoice_paid(args) -> None:
    con = connect()
    inv = con.execute("SELECT * FROM invoices WHERE id = ?", (args.id,)).fetchone()
    if not inv:
        sys.exit(f"Rechnung {args.id} nicht gefunden.")
    if inv["status"] == "draft":
        issue_invoice(con, args.id)
        inv = con.execute("SELECT * FROM invoices WHERE id = ?", (args.id,)).fetchone()
    ts = parse_date(args.paid_at) or now_unix()
    total = abs(inv["total"])
    already = inv["amount_paid_cent"] or 0
    remaining = total - already
    if remaining > 0:
        con.execute(
            "INSERT INTO invoice_payments (invoice_id, paid_at, amount_cent, source) VALUES (?, ?, ?, 'auto')",
            (args.id, ts, remaining),
        )
    con.execute(
        "UPDATE invoices SET status = 'paid', paid_at = ?, amount_paid_cent = ?, updated_at = unixepoch() "
        "WHERE id = ? AND status IN ('sent','partial','draft')",
        (ts, total, args.id),
    )
    con.commit()
    emit(
        {"id": args.id, "number": inv["number"], "paidCent": total, "status": "paid"},
        f"Als bezahlt markiert: {inv['number']} ({fmt_eur(total)})",
    )
    con.close()


# ----------------------------------------------------------------------------
# Angebote (Spiegel von src/lib/db/offers.ts)
# ----------------------------------------------------------------------------


def offer_dict(r) -> dict:
    return {
        "id": r["id"], "number": r["number"], "status": r["status"],
        "customer": snap_of(r["customer_snapshot"]).get("name"),
        "issueDate": iso(r["issue_date"]), "validUntil": iso(r["valid_until"]),
        "totalCent": r["total"], "pdfPath": r["pdf_path"], "convertedInvoiceId": r["converted_invoice_id"],
    }


def get_offer(con, offer_id: int):
    row = con.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    if not row:
        sys.exit(f"Angebot {offer_id} nicht gefunden.")
    return row


def cmd_offer_list(args) -> None:
    con = connect()
    q, params = "SELECT * FROM offers", []
    cond = []
    if args.status:
        cond.append("status = ?")
        params.append(args.status)
    if args.year:
        cond.append("CAST(strftime('%Y', issue_date, 'unixepoch') AS INT) = ?")
        params.append(args.year)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    rows = con.execute(q + " ORDER BY id DESC", params).fetchall()
    text = "\n".join(
        f"{r['id']:>3}  {r['number']:<15} {fmt_date(r['issue_date'])}  {fmt_eur(r['total']):>12}  "
        f"{r['status']:<9} {'PDF' if r['pdf_path'] else '—':<3} gültig bis {fmt_date(r['valid_until'])}"
        for r in rows
    )
    emit({"offers": [offer_dict(r) for r in rows]}, text)
    con.close()


def cmd_offer_create(args) -> None:
    con = connect()
    items = parse_items_arg(args.items)
    if not items:
        sys.exit("Keine Positionen angegeben (siehe --items).")

    def create() -> int:
        st = dict(con.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        cust = resolve_customer(con, args.customer)
        t = compute_totals(items, bool(st["is_kleinunternehmer"]), args.reverse_charge)
        issue = parse_date(args.issue_date) or now_unix()
        valid = parse_date(args.valid_until) or issue + (st["default_offer_validity_days"] or 30) * 86400
        number = next_number(con, "offer")
        cur = con.execute(
            """INSERT INTO offers (number, customer_id, customer_snapshot, issue_date, valid_until, status,
               subtotal, vat_amount, total, is_kleinunternehmer, is_reverse_charge, notes, intro_text, currency,
               exchange_rate, service_period_start, service_period_end, skonto_percent, skonto_days, pdf_language)
               VALUES (?,?,?,?,?,'draft',?,?,?,?,?,?,?,'EUR',NULL,?,?,?,?,?)""",
            (number, cust["id"], json.dumps(customer_snapshot(cust), ensure_ascii=False), issue, valid,
             t["subtotal"], t["vatAmount"], t["total"], int(bool(st["is_kleinunternehmer"])),
             int(args.reverse_charge), args.notes, args.intro_text, parse_date(args.service_start),
             parse_date(args.service_end), args.skonto_percent, args.skonto_days,
             args.pdf_language or st["default_pdf_language"]),
        )
        for i, it in enumerate(items, 1):
            con.execute(
                """INSERT INTO offer_items (offer_id, position, description, quantity, unit, unit_price, vat_rate,
                   line_total, long_description, line_period_start, line_period_end) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (cur.lastrowid, i, it["description"], it["quantity"], it["unit"], it["unitPrice"], it["vatRate"],
                 compute_line_total(it["quantity"], it["unitPrice"]), it.get("longDescription"), None, None),
            )
        con.commit()
        return cur.lastrowid

    oid, reused = create_once(con, "offer", "offers", args.idempotency_key, create)
    lines = [f"Angebot {'vorhanden' if reused else 'erstellt'}: id={oid}" + reuse_note(reused, args.idempotency_key)]
    if args.pdf:
        if reused and get_offer(con, oid)["pdf_path"]:
            lines.append("PDF bereits vorhanden — nicht neu erzeugt.")
        else:
            lines.append(f"PDF: {generate_pdf(con, 'offer', oid)[1]}")
    o = get_offer(con, oid)
    lines.append(f"{o['number']}  Gesamt: {fmt_eur(o['total'])}  gültig bis {fmt_date(o['valid_until'])}")
    emit({"offer": offer_dict(o), "reused": reused}, "\n".join(lines))
    con.close()


def cmd_offer_show(args) -> None:
    con = connect()
    o = get_offer(con, args.id)
    items = con.execute("SELECT * FROM offer_items WHERE offer_id = ? ORDER BY position", (args.id,)).fetchall()
    lines = [
        f"Angebot {o['number']}  [{o['status']}]",
        f"Kunde:        {snap_of(o['customer_snapshot']).get('name')}",
        f"Datum:        {fmt_date(o['issue_date'])}  ·  gültig bis {fmt_date(o['valid_until'])}",
        "", items_text(items), "",
        f"Netto: {fmt_eur(o['subtotal'])}  ·  USt: {fmt_eur(o['vat_amount'])}  ·  Gesamt: {fmt_eur(o['total'])}",
    ]
    if o["converted_invoice_id"]:
        lines.append(f"Umgewandelt in Rechnung id={o['converted_invoice_id']}")
    if o["pdf_path"]:
        lines.append(f"PDF:   {o['pdf_path']}")
    emit({"offer": {**offer_dict(o), "subtotalCent": o["subtotal"], "vatAmountCent": o["vat_amount"],
                    "introText": o["intro_text"], "notes": o["notes"], "items": items_dict(items)}}, "\n".join(lines))
    con.close()


def offer_transition(status: str, stamp: str, allowed: tuple):
    """Statuswechsel; gleicher Zielstatus = No-op (idempotent), unzulässige Herkunft = Fehler."""
    def run(args) -> None:
        con = connect()
        o = get_offer(con, args.id)
        if o["status"] == status:
            note = "bereits"
        elif o["status"] not in allowed:
            sys.exit(f"Angebot {o['number']}: Übergang '{o['status']}' -> '{status}' nicht erlaubt.")
        else:
            con.execute(
                f"UPDATE offers SET status = ?, {stamp} = unixepoch(), updated_at = unixepoch() WHERE id = ?",
                (status, args.id),
            )
            con.commit()
            note = "jetzt"
        emit({"offer": offer_dict(get_offer(con, args.id)), "changed": note == "jetzt"},
             f"Angebot {o['number']}: {note} {status}")
        con.close()
    return run


def cmd_offer_convert(args) -> None:
    con = connect()
    o = get_offer(con, args.id)
    if o["converted_invoice_id"]:
        inv = con.execute("SELECT * FROM invoices WHERE id = ?", (o["converted_invoice_id"],)).fetchone()
        emit({"invoice": inv_dict(inv), "reused": True},
             f"Angebot {o['number']} bereits umgewandelt: Rechnung id={inv['id']}")
        con.close()
        return
    if o["status"] == "rejected":
        sys.exit("Abgelehnte Angebote können nicht umgewandelt werden.")
    rows = con.execute("SELECT * FROM offer_items WHERE offer_id = ? ORDER BY position", (args.id,)).fetchall()
    items = [
        {"description": r["description"], "quantity": r["quantity"], "unit": r["unit"], "unitPrice": r["unit_price"],
         "vatRate": r["vat_rate"], "longDescription": r["long_description"],
         "linePeriodStart": r["line_period_start"], "linePeriodEnd": r["line_period_end"]}
        for r in rows
    ]
    iid = create_invoice(
        con, str(o["customer_id"]), items, payment_terms=args.payment_terms,
        reverse_charge="intra_eu" if o["is_reverse_charge"] else "none",
        service_start=o["service_period_start"], service_end=o["service_period_end"],
        skonto_percent=o["skonto_percent"], skonto_days=o["skonto_days"], pdf_language=o["pdf_language"],
        notes=o["notes"],
    )
    con.execute(
        "UPDATE offers SET status = 'accepted', accepted_at = COALESCE(accepted_at, unixepoch()), "
        "converted_invoice_id = ?, updated_at = unixepoch() WHERE id = ?",
        (iid, args.id),
    )
    con.commit()
    inv = con.execute("SELECT * FROM invoices WHERE id = ?", (iid,)).fetchone()
    emit({"invoice": inv_dict(inv), "reused": False},
         f"Angebot {o['number']} -> Rechnungsentwurf id={iid} ({fmt_eur(inv['total'])}); Angebot ist 'accepted'")
    con.close()


def cmd_offer_pdf(args) -> None:
    con = connect()
    number, path = generate_pdf(con, "offer", args.id)
    emit({"number": number, "pdfPath": path}, f"{number}: {path}")
    con.close()


# ----------------------------------------------------------------------------
# Mahnungen (Spiegel von src/lib/db/reminders.ts)
# ----------------------------------------------------------------------------


def reminder_dict(r) -> dict:
    snap = snap_of(r["invoice_snapshot"])
    return {
        "id": r["id"], "number": r["number"], "invoiceId": r["invoice_id"],
        "invoiceNumber": snap.get("invoiceNumber"), "customer": snap.get("customerName"),
        "level": r["level"], "levelLabel": REMINDER_LABELS.get(r["level"]), "status": r["status"],
        "issueDate": iso(r["issue_date"]), "dueDate": iso(r["due_date"]), "feeCent": r["fee_cents"],
        "interestCent": r["interest_cents"], "totalDueCent": r["total_due_cents"], "pdfPath": r["pdf_path"],
    }


def get_reminder(con, reminder_id: int):
    row = con.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    if not row:
        sys.exit(f"Mahnung {reminder_id} nicht gefunden.")
    return row


def cmd_reminder_list(args) -> None:
    con = connect()
    if args.invoice:
        rows = con.execute("SELECT * FROM reminders WHERE invoice_id = ? ORDER BY id DESC", (args.invoice,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM reminders ORDER BY id DESC").fetchall()
    text = "\n".join(
        f"{r['id']:>3}  {r['number']:<15} Stufe {r['level']}  Rechnung {snap_of(r['invoice_snapshot']).get('invoiceNumber')}  "
        f"{fmt_eur(r['total_due_cents']):>12}  {r['status']:<6} {'PDF' if r['pdf_path'] else '—'}"
        for r in rows
    )
    emit({"reminders": [reminder_dict(r) for r in rows]}, text)
    con.close()


def cmd_reminder_create(args) -> None:
    con = connect()

    def create() -> int:
        inv = con.execute("SELECT * FROM invoices WHERE id = ?", (args.invoice,)).fetchone()
        if not inv:
            sys.exit(f"Rechnung {args.invoice} nicht gefunden.")
        if inv["status"] != "sent":
            sys.exit("Mahnungen können nur für versendete Rechnungen (Status 'sent') erstellt werden.")
        if inv["is_credit_note"]:
            sys.exit("Für Stornorechnungen können keine Mahnungen erstellt werden.")
        st = dict(con.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        lvl = args.level
        issue = parse_date(args.issue_date) or now_unix()
        due = parse_date(args.due_date) or issue + st[f"reminder_days_l{lvl}"] * 86400
        fee = eur_to_cent(args.fee) if args.fee is not None else st[f"reminder_fee_l{lvl}_cents"]
        interest = eur_to_cent(args.interest) if args.interest is not None else 0
        c = snap_of(inv["customer_snapshot"])
        snap = {
            "invoiceNumber": inv["number"], "invoiceIssueDate": inv["issue_date"], "invoiceDueDate": inv["due_date"],
            "customerName": c.get("name") or "—", "customerStreet": c.get("street") or "",
            "customerPostalCode": c.get("postalCode") or "", "customerCity": c.get("city") or "",
            "customerCountry": c.get("country") or "DE", "customerContactPerson": c.get("contactPerson"),
        }
        number = next_number(con, "reminder")
        cur = con.execute(
            """INSERT INTO reminders (number, invoice_id, invoice_snapshot, level, issue_date, due_date, fee_cents,
               interest_cents, original_total_cents, total_due_cents, status, body_text, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?)""",
            (number, inv["id"], json.dumps(snap, ensure_ascii=False), lvl, issue, due, fee, interest, inv["total"],
             inv["total"] + fee + interest, args.body or st[f"reminder_text_l{lvl}"], args.notes),
        )
        con.commit()
        return cur.lastrowid

    rid, reused = create_once(con, "reminder", "reminders", args.idempotency_key, create)
    lines = [f"Mahnung {'vorhanden' if reused else 'erstellt'}: id={rid}" + reuse_note(reused, args.idempotency_key)]
    if args.pdf:
        if reused and get_reminder(con, rid)["pdf_path"]:
            lines.append("PDF bereits vorhanden — nicht neu erzeugt.")
        else:
            lines.append(f"PDF: {generate_pdf(con, 'reminder', rid)[1]}")
    r = get_reminder(con, rid)
    lines.append(f"{r['number']}  Stufe {r['level']}  fällig {fmt_date(r['due_date'])}  Forderung gesamt {fmt_eur(r['total_due_cents'])}")
    emit({"reminder": reminder_dict(r), "reused": reused}, "\n".join(lines))
    con.close()


def cmd_reminder_send(args) -> None:
    con = connect()
    r = get_reminder(con, args.id)
    changed = r["status"] == "draft"
    if changed:
        con.execute(
            "UPDATE reminders SET status = 'sent', sent_at = unixepoch(), updated_at = unixepoch() WHERE id = ?",
            (args.id,),
        )
        con.commit()
    emit({"reminder": reminder_dict(get_reminder(con, args.id)), "changed": changed},
         f"Mahnung {r['number']}: {'jetzt' if changed else 'bereits'} sent")
    con.close()


def cmd_reminder_pdf(args) -> None:
    con = connect()
    number, path = generate_pdf(con, "reminder", args.id)
    emit({"number": number, "pdfPath": path}, f"{number}: {path}")
    con.close()


def cmd_stats(_args) -> None:
    con = connect()
    year = datetime.now().year
    rev = con.execute(
        "SELECT COALESCE(SUM(total),0) FROM invoices "
        "WHERE status NOT IN ('cancelled','draft') "
        "AND strftime('%Y', issue_date, 'unixepoch') = ?",
        (str(year),),
    ).fetchone()[0]
    n_open, open_cent = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(total),0) FROM invoices WHERE status IN ('sent','partial')"
    ).fetchone()
    emit(
        {"year": year, "revenueCent": rev, "openCount": n_open, "openCent": open_cent},
        f"Umsatz {year} (ohne Entwurf/Storno): {fmt_eur(rev)}\nOffene Forderungen: {n_open} · {fmt_eur(open_cent)}",
    )
    con.close()


GUIDE = """\
Headless-Steuerung der Zettel-DB (Rechnungen, Angebote, Mahnungen, Kunden für deutsche Freelancer).
Standard-Ausgabe ist deutscher Klartext. Mit --json (vor oder nach dem Befehl) kommt genau EIN JSON-Objekt auf
stdout: {"ok": true, ...Daten} bzw. {"ok": false, "error": "..."} (Exit-Code 1). Ohne --json gehen Fehler
als Text auf stderr, Exit-Code != 0. In JSON sind Beträge Cent-Integer (*Cent), Daten ISO YYYY-MM-DD.

TYPISCHER ABLAUF
  Rechnung   customers -> invoice:create -> invoice:issue -> invoice:pdf -> invoice:paid
             Kurzform: invoice:create ... --pdf   (impliziert --issue)
  Angebot    offer:create -> offer:send -> offer:accept -> offer:convert (erzeugt Rechnungsentwurf)
  Mahnung    invoices --overdue -> reminder:create --invoice <id> --level 1 -> reminder:send / reminder:pdf
  Kunde      customer:create --name "ACME GmbH" --city Berlin   (Nummer K-0001 wird vergeben)
  Beispiel   zettel invoice:create --customer K-0001 --items "Beratung|2|Std|90|19" \\
                 --idempotency-key mai-beratung --json

IDEMPOTENZ  (WICHTIG bei Automatisierung / Wiederholungen)
  * Alle *:create-Befehle akzeptieren --idempotency-key <beliebiger-String>. Gleicher Key (pro DB + Typ)
    legt NICHTS neu an, sondern liefert den bestehenden Datensatz zurück (JSON: "reused": true).
    Immer einen Key setzen, wenn ein Aufruf wiederholt werden könnte (Timeout, Retry, Agent-Neustart).
  * Ohne Key legt jeder Aufruf einen neuen Datensatz an (und verbraucht bei Angeboten/Mahnungen eine Nummer).
  * Keys liegen in <AppData>/cli-idempotency.json, nicht in der DB; gelöschte Datensätze werden ignoriert.
  * Natürlich idempotent: invoice:issue, invoice:paid, offer:send/accept/reject (gleicher Status = No-op),
    reminder:send, offer:convert (bereits umgewandelt -> liefert die Rechnung). --pdf bei einem wiederholten
    create mit Key erzeugt das PDF nur, wenn noch keins existiert; invoice:pdf/offer:pdf/reminder:pdf
    erzeugen immer neu (altes PDF -> Versionen/).

WICHTIG
  * <id> ist immer die DB-id aus der Ausgabe von *:create / den Listen, NICHT die Dokumentnummer.
    Rechnungsentwürfe haben noch keine Nummer ("Entwurf #abcd"); Nummern werden erst bei issue/pdf/paid
    gezogen -> Entwürfe verbrennen keine Nummer. Angebote/Mahnungen bekommen ihre Nummer sofort.
  * Mahnungen nur für Rechnungen mit Status 'sent' (nicht partial/paid/draft), nicht für Stornorechnungen.
  * Die CLI kann NICHT: Dokumente bearbeiten/löschen/stornieren, Kunden ändern, Ausgaben/Lieferanten,
    Einstellungen. Dafür die App benutzen.

FORMATE
  --items   'Beschreibung|Menge|Einheit|Preis-in-EUR[|MwSt-%][|Langtext]', Positionen mit ';'
            Menge/Preis mit Punkt oder Komma ("1,5"). Einheit leer = "Std".
            MwSt-% ganzzahlig und OPTIONAL mit Default 0 -> für 19 % ausdrücklich "19" angeben.
            Ist in den Einstellungen Kleinunternehmer aktiv oder Reverse-Charge gesetzt, ist der
            MwSt-Betrag automatisch 0. Beträge intern in Cent (kein Float).
  Datum     YYYY-MM-DD, TT.MM.JJJJ oder Unix-Timestamp. Default Ausstellungsdatum: heute.
  --customer  DB-id, Kundennummer (K-0001) oder exakter Name (Groß/Klein egal).
  --fee/--interest (Mahnung)  EUR-Betrag, Punkt oder Komma.

DATENBANK / UMGEBUNG
  DB-Auswahl wie die App: sandbox.flag -> zettel-sandbox.db, sonst aktiver Mandant, sonst zettel.db im
  AppData-Ordner. Sandbox-PDFs landen in ~/Documents/Zettel-Sandbox/<Rechnungen|Angebote|Mahnungen>.
  Nicht parallel zur laufenden App SCHREIBEN (SQLite single-writer, Timeout 10 s); Lesen ist unkritisch.
  PDF braucht den Sidecar: ZETTEL_SIDECAR=<Pfad zu zettel-sidecar(.exe)> oder installierte App bzw.
  sidecar/.venv im Repo. `zettel status` zeigt, was gefunden wurde. Zuerst `status` aufrufen.
"""


def main() -> None:
    global JSON_MODE
    json_help = "Ausgabe als ein JSON-Objekt (vor oder nach dem Befehl erlaubt)"
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", dest="json_sub", action="store_true", help=json_help)
    ap = argparse.ArgumentParser(prog="zettel", description=GUIDE, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", dest="json_top", action="store_true", help=json_help)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="BEFEHL")

    def add(name, func, help, idem=False):
        p = sub.add_parser(name, help=help, parents=[common])
        if idem:
            p.add_argument("--idempotency-key", help="Wiederholung mit gleichem Key legt nichts neu an")
        p.set_defaults(func=func)
        return p

    def with_id(name, func, help, id_help="DB-id"):
        p = add(name, func, help)
        p.add_argument("id", type=int, help=id_help)
        return p

    add("status", cmd_status, "DB-Pfad, Sandbox-Flag, Anzahl Rechnungen/Kunden, Sidecar-Fundort (zuerst aufrufen)")

    add("customers", cmd_customers, "Kunden listen: id, Kundennummer, Name, Ort, E-Mail")
    p = add("customer:create", cmd_customer_create, "Kunden anlegen (Nummer K-NNNN automatisch)", idem=True)
    p.add_argument("--name", required=True)
    p.add_argument("--number", help="eigene Kundennummer statt K-NNNN (muss eindeutig sein)")
    p.add_argument("--contact-person")
    p.add_argument("--street")
    p.add_argument("--postal-code")
    p.add_argument("--city")
    p.add_argument("--country", help="ISO-Code, Default DE")
    p.add_argument("--email")
    p.add_argument("--phone")
    p.add_argument("--vat-id", help="USt-IdNr.")
    p.add_argument("--notes")

    p = add("invoices", cmd_invoice_list, "Rechnungen listen (neueste zuerst): id, Nummer, Datum, Brutto, Status, PDF, Fälligkeit")
    p.add_argument("--status", choices=["draft", "sent", "partial", "paid", "cancelled"])
    p.add_argument("--year", type=int, help="nach Ausstellungsjahr filtern")
    p.add_argument("--overdue", action="store_true", help="nur überfällige (sent/partial, Fälligkeit überschritten); JSON enthält reminderLevels")

    p = add("invoice:create", cmd_invoice_create, "Rechnungsentwurf anlegen (ohne Nummer); gibt die DB-id aus", idem=True)
    p.add_argument("--customer", required=True, help="DB-id, Kundennummer oder Name")
    p.add_argument("--items", required=True,
                   help="'Desc|Menge|Einheit|Preis€[|MwSt%%][|Langtext]' — mehrere mit ';'. MwSt-Default 0!")
    p.add_argument("--issue-date", help="Ausstellungsdatum (Default heute)")
    p.add_argument("--due-date", help="Fälligkeit (Default: Ausstellung + Zahlungsziel)")
    p.add_argument("--delivery-date", help="Lieferdatum (entweder das oder Leistungszeitraum; die CLI prüft das nicht, die App erzwingt es)")
    p.add_argument("--service-start", help="Leistungszeitraum Beginn")
    p.add_argument("--service-end", help="Leistungszeitraum Ende")
    p.add_argument("--payment-terms", type=int, help="Zahlungsziel in Tagen (Default aus Einstellungen, sonst 14)")
    p.add_argument("--reverse-charge", default="none", choices=["none", "intra_eu", "third_country"],
                   help="Steuerschuldumkehr: innergemeinschaftlich bzw. Drittland; setzt MwSt auf 0 (Default none)")
    p.add_argument("--skonto-percent", type=float, help="Skonto in %% (nur zusammen mit --skonto-days)")
    p.add_argument("--skonto-days", type=int, help="Skonto-Frist in Tagen ab Ausstellung")
    p.add_argument("--pdf-language", default="de", choices=["de", "en"], help="Sprache des PDFs (Default de)")
    p.add_argument("--notes", help="Freitext auf der Rechnung")
    p.add_argument("--issue", action="store_true", help="Direkt bereitstellen (Nummer ziehen)")
    p.add_argument("--pdf", action="store_true", help="PDF generieren (impliziert --issue, braucht Sidecar)")

    with_id("invoice:show", cmd_invoice_show, "Eine Rechnung mit Positionen, Summen und PDF-Pfad anzeigen", "DB-id (nicht die Rechnungsnummer)")
    with_id("invoice:pdf", cmd_invoice_pdf, "ZUGFeRD-PDF erzeugen (Entwurf wird vorher bereitgestellt); altes PDF wird archiviert")
    p = with_id("invoice:issue", cmd_invoice_issue, "Entwurf bereitstellen = echte Rechnungsnummer ziehen, Status sent (sonst No-op)")
    p.add_argument("--sent-at", help="Versanddatum (Rückdatierung), Default jetzt")
    p = with_id("invoice:paid", cmd_invoice_paid, "Als vollständig bezahlt markieren (Restbetrag wird als Zahlung protokolliert)")
    p.add_argument("--paid-at", help="Zahldatum (Rückdatierung), Default jetzt")

    p = add("offers", cmd_offer_list, "Angebote listen (neueste zuerst)")
    p.add_argument("--status", choices=["draft", "sent", "accepted", "rejected", "expired"])
    p.add_argument("--year", type=int, help="nach Ausstellungsjahr filtern")
    p = add("offer:create", cmd_offer_create, "Angebotsentwurf anlegen (Nummer AN-… sofort); gibt die DB-id aus", idem=True)
    p.add_argument("--customer", required=True, help="DB-id, Kundennummer oder Name")
    p.add_argument("--items", required=True, help="wie bei invoice:create (MwSt-Default 0!)")
    p.add_argument("--issue-date", help="Default heute")
    p.add_argument("--valid-until", help="Gültig bis (Default: Ausstellung + Gültigkeit aus Einstellungen, sonst 30 Tage)")
    p.add_argument("--reverse-charge", action="store_true", help="Steuerschuldumkehr (MwSt 0)")
    p.add_argument("--intro-text", help="Einleitungstext über den Positionen")
    p.add_argument("--notes")
    p.add_argument("--service-start")
    p.add_argument("--service-end")
    p.add_argument("--skonto-percent", type=float)
    p.add_argument("--skonto-days", type=int)
    p.add_argument("--pdf-language", choices=["de", "en"], help="Default aus Einstellungen")
    p.add_argument("--pdf", action="store_true", help="PDF generieren (braucht Sidecar)")
    with_id("offer:show", cmd_offer_show, "Ein Angebot mit Positionen und Summen anzeigen")
    with_id("offer:send", offer_transition("sent", "sent_at", ("draft",)), "Als gesendet markieren (draft -> sent)")
    with_id("offer:accept", offer_transition("accepted", "accepted_at", ("sent", "draft")), "Als angenommen markieren")
    with_id("offer:reject", offer_transition("rejected", "rejected_at", ("sent", "draft")), "Als abgelehnt markieren")
    p = with_id("offer:convert", cmd_offer_convert, "In Rechnungsentwurf umwandeln; Angebot wird 'accepted' (wiederholbar)")
    p.add_argument("--payment-terms", type=int, help="Zahlungsziel in Tagen (Default aus Einstellungen)")
    with_id("offer:pdf", cmd_offer_pdf, "Angebots-PDF erzeugen (PDF/A-3b ohne XML); altes PDF wird archiviert")

    p = add("reminders", cmd_reminder_list, "Mahnungen listen (neueste zuerst)")
    p.add_argument("--invoice", type=int, help="nur Mahnungen zu dieser Rechnungs-id")
    p = add("reminder:create", cmd_reminder_create,
            "Mahnung-Entwurf zu einer versendeten Rechnung anlegen (Nummer MA-… sofort)", idem=True)
    p.add_argument("--invoice", type=int, required=True, help="DB-id der Rechnung (Status sent)")
    p.add_argument("--level", type=int, choices=[1, 2, 3], required=True, help="1 Zahlungserinnerung, 2 Mahnung, 3 Letzte Mahnung")
    p.add_argument("--issue-date", help="Default heute")
    p.add_argument("--due-date", help="Default: Ausstellung + Frist der Stufe aus Einstellungen")
    p.add_argument("--fee", help="Mahngebühr in EUR (Default aus Einstellungen je Stufe)")
    p.add_argument("--interest", help="Verzugszinsen in EUR (Default 0)")
    p.add_argument("--body", help="Text (Default: Standardtext der Stufe aus Einstellungen)")
    p.add_argument("--notes")
    p.add_argument("--pdf", action="store_true", help="PDF generieren (braucht Sidecar)")
    with_id("reminder:send", cmd_reminder_send, "Als gesendet markieren (draft -> sent)")
    with_id("reminder:pdf", cmd_reminder_pdf, "Mahnungs-PDF erzeugen; altes PDF wird archiviert")

    add("stats", cmd_stats, "Umsatz des laufenden Jahres (ohne Entwurf/Storno) und offene Forderungen")

    args = ap.parse_args()
    JSON_MODE = args.json_top or args.json_sub
    try:
        args.func(args)
    except SystemExit as e:
        if JSON_MODE and isinstance(e.code, str):
            print(json.dumps({"ok": False, "error": e.code}, ensure_ascii=False))
            sys.exit(1)
        raise
    except sqlite3.Error as e:
        msg = f"DB-Fehler: {e}"
        if JSON_MODE:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
            sys.exit(1)
        sys.exit(msg)


if __name__ == "__main__":
    main()
