# Zettel CLI (`cli/zettel.py`)

Headless-Steuerung der Zettel-Datenbank für Automatisierung (z. B. Agenten,
Scripts, Cron). Nur Python-stdlib, keine Abhängigkeiten.

## DB-Auflösung

Wie die App (`src-tauri/src/tenants.rs`): `sandbox.flag` → Sandbox-DB,
sonst aktiver Tenant aus `tenants.json`, sonst Standard-`zettel.db`.

**Nicht parallel zur laufenden App schreiben** (SQLite single-writer).
Lesende Befehle sind unproblematisch.

## Befehle

```
python cli/zettel.py status                          # DB, Sandbox, Sidecar (zuerst aufrufen)
python cli/zettel.py stats                           # Umsatz / offene Posten

# Kunden
python cli/zettel.py customers
python cli/zettel.py customer:create --name "ACME GmbH" [--city --street --postal-code --email --vat-id ...]

# Rechnungen
python cli/zettel.py invoices [--status X] [--year N] [--overdue]
python cli/zettel.py invoice:create --customer K-0001 \
    --items "IT-Vor-Ort-Service|2|Std|90|19;Remote-Support|1.5|Std|90|19" \
    [--issue-date 2026-08-27] [--issue] [--pdf]
python cli/zettel.py invoice:show|issue|pdf|paid <id>

# Angebote
python cli/zettel.py offers [--status X] [--year N]
python cli/zettel.py offer:create --customer K-0001 --items "..." [--valid-until ...] [--pdf]
python cli/zettel.py offer:show|send|accept|reject|convert|pdf <id>   # convert -> Rechnungsentwurf

# Mahnungen
python cli/zettel.py reminders [--invoice <id>]
python cli/zettel.py reminder:create --invoice <id> --level 1|2|3 [--fee 5,00] [--pdf]
python cli/zettel.py reminder:send|pdf <id>
```

Positions-Format: `Beschreibung|Menge|Einheit|Einzelpreis-Euro[|MwSt%][|Langtext]`,
mehrere Positionen mit `;`. **MwSt ohne Angabe = 0**, für 19 % also `19` schreiben.

**`--json`** (vor oder nach dem Befehl): genau ein JSON-Objekt auf stdout,
`{"ok": true, ...}` bzw. `{"ok": false, "error": "..."}` mit Exit-Code 1. Beträge als
Cent-Integer (`*Cent`), Daten ISO.

**`--idempotency-key <key>`** bei allen `*:create`: gleicher Key (pro DB und Typ) legt nichts
neu an, sondern liefert den bestehenden Datensatz (`"reused": true`). Wichtig für Retries und
Agenten. Keys liegen in `<AppData>/cli-idempotency.json` (nicht in der DB, keine Migration);
stirbt der Prozess exakt zwischen COMMIT und Speichern, ist ein Duplikat möglich.
Natürlich idempotent sind außerdem `invoice:issue|paid`, `offer:send|accept|reject|convert`
und `reminder:send`.

Die vollständige Referenz (Ablauf, Formate, Defaults, Grenzen) steht in
`python cli/zettel.py -h` und `… <befehl> -h` — bewusst so geschrieben, dass ein LLM-Agent
nur damit arbeiten kann. **Nicht möglich:** Dokumente bearbeiten/löschen/stornieren, Kunden
ändern, Ausgaben/Lieferanten, Einstellungen.

Test: `cd cli && python -m pytest test_zettel.py` (frische DB aus den Migrationen, ohne Sidecar).

## Konventions-Parität zur App

Der CLI repliziert bewusst die App-Logik aus `src/lib/db/{invoices,offers,reminders,queries}.ts`:

- **Draft-Placeholder** `DRAFT-<hex>` — kein Nummern-Slot wird verbrannt
- **Lazy Numbering**: echte `RE-{YYYY}-{NNNN}` erst bei `issue`/`pdf`
- **Per-Line-Rundung** (`computeTotals`), Cent-Integer überall
- **markPaid** protokolliert Restbetrag als `invoice_payments`-Eintrag (`auto`)
- **PDF-Versionierung**: existierendes PDF wird vor Regeneration nach
  `Versionen/<stem>__<mtime>.pdf` verschoben (wie `archive_pdf_version`)
- **Kunden** (`K-` = MAX(Suffix)+1), **Angebote/Mahnungen**: eager Numbering über `*_number_counter`,
  Mahnung nur für Rechnungen mit Status `sent`; PDF-Payloads spiegeln `src/lib/sidecar/{invoice,offer,reminder}.ts`
- **Sidecar**: JSON-RPC über stdin/stdout, Dev-Venv oder `ZETTEL_SIDECAR`-Binary

Bei Änderungen an der App-Logik (Migrationen, Payload-Format) müssen beide
Stellen angefasst werden.
