"""Versioned source snapshots and workflow state."""

import csv
import hashlib
import io
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


SOURCES = {
    "merchant": ("id", "kind", "charge_id", "refund_id", "gross", "fee", "cents"),
    "processor": ("id", "kind", "charge_id", "gross", "fee", "trace", "cents"),
    "bank": ("id", "trace", "cents", "posted_at"),
}
SUPPORT_FIELDS = ("id", "posted_at", "text")
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY, account TEXT NOT NULL, case_id TEXT NOT NULL,
    version INTEGER NOT NULL, cutoff TEXT NOT NULL, digest TEXT NOT NULL,
    feeds TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account, case_id, version)
);
CREATE TABLE IF NOT EXISTS source_rows (
    snapshot INTEGER NOT NULL REFERENCES snapshots(id),
    source TEXT NOT NULL, line INTEGER NOT NULL, row_id TEXT NOT NULL,
    raw TEXT NOT NULL, file_digest TEXT NOT NULL,
    PRIMARY KEY(snapshot, source, line), UNIQUE(snapshot, source, row_id)
);
CREATE TABLE IF NOT EXISTS quarantines (
    account TEXT NOT NULL, case_id TEXT NOT NULL, version INTEGER NOT NULL,
    digest TEXT NOT NULL, reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(account, case_id, version, digest)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, snapshot INTEGER NOT NULL REFERENCES snapshots(id),
    revision TEXT NOT NULL, mode TEXT NOT NULL, evidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', lease TEXT, expires REAL, result TEXT,
    UNIQUE(snapshot, revision, mode)
);
CREATE TABLE IF NOT EXISTS steps (
    job INTEGER NOT NULL REFERENCES jobs(id), attempt TEXT NOT NULL,
    sequence INTEGER NOT NULL, detail TEXT NOT NULL, PRIMARY KEY(job, attempt, sequence)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, job INTEGER NOT NULL REFERENCES jobs(id),
    request_key TEXT NOT NULL UNIQUE, action TEXT NOT NULL, actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(job)
);
CREATE TABLE IF NOT EXISTS followups (
    review INTEGER PRIMARY KEY REFERENCES reviews(id), instruction TEXT NOT NULL,
    completed_snapshot INTEGER REFERENCES snapshots(id)
);
PRAGMA user_version = 1;
"""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value).hexdigest()


@contextmanager
def database(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            db.executescript(SCHEMA)
        elif version != 1:
            raise ValueError(f"Unsupported database schema {version}")
        with db:
            yield db
    finally:
        db.close()


def identity(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError(f"Invalid identifier {value!r}")
    return value


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("Timestamps must use YYYY-MM-DDTHH:MM:SSZ")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def cents(value):
    if not re.fullmatch(r"-?[0-9]{1,13}", value):
        raise ValueError("Expected integer USD cents")
    return int(value)


def normalize(source, row):
    row_id = identity(row["id"])
    if source == "bank":
        if cents(row["cents"]) <= 0:
            raise ValueError("Bank credits must be positive")
        return {"id": row_id, "trace": identity(row["trace"]) if row["trace"] else None,
                "cents": cents(row["cents"]), "posted_at": timestamp(row["posted_at"])}
    kind = row["kind"]
    if source == "processor" and kind == "payout":
        return {"payout_id": row_id, "trace": identity(row["trace"]) if row["trace"] else None,
                "header_cents": cents(row["cents"])}
    if source == "merchant" and kind == "receipt":
        if cents(row["cents"]) >= 0:
            raise ValueError("Refund receipts must be negative")
        return {"id": row_id, "refund_id": identity(row["refund_id"]),
                "charge_id": identity(row["charge_id"]), "cents": cents(row["cents"])}
    gross, fee = cents(row["gross"]), cents(row["fee"])
    if fee < 0 or not ((kind == "refund" and gross < 0) or
                      (kind == "fee" and gross == 0 and fee > 0) or
                      (kind == "charge" and gross > fee)):
        raise ValueError("Invalid charge, refund or fee")
    event = {"id": row_id, "kind": kind, "gross": gross, "fee": fee}
    if kind == "refund":
        event["charge_id"] = identity(row["charge_id"])
    return event


def read_batch(manifest):
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    account, case_id = identity(meta["account"]), identity(meta["case_id"])
    version, cutoff = meta["version"], timestamp(meta["cutoff"])
    if meta["schema"] != 1 or meta["currency"] != "USD" or type(version) is not int or version < 1:
        raise ValueError("Expected schema 1, USD, and a positive integer snapshot version")
    if meta.get("supersedes") != (version - 1 if version > 1 else None):
        raise ValueError("Declare the preceding version for a superseding snapshot")
    scope = {"account": account, "case_id": case_id, "currency": "USD", "cutoff": cutoff}
    scopes, rows, files = {}, [], {}
    sources = dict(SOURCES)
    if "support" in meta["sources"]:
        sources["support"] = SUPPORT_FIELDS
    for source, fields in sources.items():
        spec = meta["sources"][source]
        name = spec["file"]
        if Path(name).name != name or not name.endswith(".csv"):
            raise ValueError("Source must name a CSV alongside the manifest")
        coverage = timestamp(spec["coverage"])
        scopes[source] = {**scope, "coverage": coverage, "complete": coverage >= cutoff}
        path = (manifest.parent / name).resolve()
        if path.parent != manifest.parent.resolve():
            raise ValueError("Source must resolve alongside the manifest")
        raw = path.read_bytes()
        files[source] = digest(raw)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError(f"{source}: unexpected CSV header")
        parsed, identities = [], set()
        for record in reader:
            try:
                row_id = record["id"]
                if None in record or any(v is None for v in record.values()) or row_id in identities:
                    raise ValueError("Wrong column count or repeated row ID")
                if source == "support":
                    body = record["text"]
                    if not body.strip() or len(body) > 2000:
                        raise ValueError("Expected 1 to 2000 characters of support text")
                    normalized = {"id": identity(row_id), "posted_at": timestamp(record["posted_at"]), "text": body}
                else:
                    normalized = normalize(source, record)
                identities.add(row_id)
                parsed.append((record.get("kind"), normalized))
                rows.append((source, reader.line_num, row_id, encode(record), files[source]))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{source} row ending at line {reader.line_num}: {exc}") from exc
        feed = scopes[source]
        if source == "support":
            feed["notes"] = [row for _, row in parsed if row["posted_at"] <= cutoff]
        elif source == "bank":
            feed["credits"] = [row for _, row in parsed if row["posted_at"] <= cutoff]
        elif source == "merchant":
            feed["entries"] = [row for kind, row in parsed if kind != "receipt"]
            feed["refund_receipts"] = [row for kind, row in parsed if kind == "receipt"]
        else:
            headers = [row for kind, row in parsed if kind == "payout"]
            if len(headers) != 1 or headers[0]["header_cents"] <= 0:
                raise ValueError("processor: expected one positive payout header")
            feed.update(headers[0], items=[row for kind, row in parsed if kind != "payout"])
    return scope, version, digest(encode([meta, files]).encode()), scopes, rows


def import_batch(path, manifest):
    scope, version, fingerprint, feeds, rows = read_batch(Path(manifest))
    account, case_id, cutoff = (scope[k] for k in ("account", "case_id", "cutoff"))
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM snapshots WHERE account=? AND case_id=? AND version=?",
                         (account, case_id, version)).fetchone()
        if old:
            if old["digest"] == fingerprint:
                return {"snapshot": old["id"], "state": "duplicate"}
            db.execute("INSERT OR IGNORE INTO quarantines(account,case_id,version,digest,reason) VALUES (?,?,?,?,?)",
                       (account, case_id, version, fingerprint, "version already exists with different data"))
            return {"snapshot": old["id"], "state": "quarantined"}
        timeline = db.execute("SELECT version,cutoff,feeds FROM snapshots WHERE account=? AND case_id=?",
                              (account, case_id))
        for item in timeline:
            if ((version > item["version"] and cutoff < item["cutoff"]) or
                    (version < item["version"] and cutoff > item["cutoff"])):
                raise ValueError("Snapshot cutoffs must follow version order")
            if json.loads(item["feeds"])["processor"]["payout_id"] != feeds["processor"]["payout_id"]:
                raise ValueError("Snapshot cannot replace its payout identity")
        payout_id = feeds["processor"]["payout_id"]
        owner = db.execute("""SELECT case_id FROM snapshots
            WHERE account=? AND json_extract(feeds,'$.processor.payout_id')=? AND case_id<>? LIMIT 1""",
                           (account, payout_id, case_id)).fetchone()
        if owner:
            raise ValueError("Payout already belongs to another case in this account")
        cursor = db.execute("INSERT INTO snapshots(account,case_id,version,cutoff,digest,feeds) VALUES (?,?,?,?,?,?)",
                            (account, case_id, version, cutoff, fingerprint, encode(feeds)))
        snapshot = cursor.lastrowid
        db.executemany("INSERT INTO source_rows VALUES (?,?,?,?,?,?)", [(snapshot, *row) for row in rows])
        return {"snapshot": snapshot, "state": "imported"}


def heads(db, account=None):
    return db.execute("""SELECT s.* FROM snapshots s
        WHERE (? IS NULL OR s.account=?) AND NOT EXISTS (
            SELECT 1 FROM snapshots n WHERE n.account=s.account AND n.case_id=s.case_id AND n.version>s.version)
        ORDER BY s.account,s.case_id""", (account, account)).fetchall()


def current_case(db, snapshot):
    row = db.execute("SELECT * FROM snapshots WHERE id=?", (snapshot,)).fetchone()
    if row is None:
        raise ValueError("Unknown snapshot")
    feeds = json.loads(row["feeds"])
    processor = feeds["processor"]
    claims = set()
    for other in heads(db, row["account"]):
        if other["case_id"] != row["case_id"]:
            competing = json.loads(other["feeds"])
            trace = competing["processor"]["trace"]
            claims.update(c["id"] for c in competing["bank"]["credits"] if trace and c["trace"] == trace)
    colliding = sorted(c["id"] for c in feeds["bank"]["credits"] if c["id"] in claims and
                       ("support" in feeds or processor["trace"] and c["trace"] == processor["trace"]))
    revision = digest(encode([row["digest"], colliding]).encode())
    return row, feeds, colliding, revision
