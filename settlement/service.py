"""Reconciliation jobs, leased investigation and version-bound review."""

import json
import secrets
import time

from settlement.agent import investigate
from settlement.analysis import NEXT, REQUESTS, baseline, facts
from settlement.storage import current_case, database, encode, heads


def queue(path, snapshot, mode):
    if mode not in {"baseline", "live", "replay"}:
        raise ValueError("Investigation mode must be baseline, live or replay")
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        _, feeds, collisions, revision = current_case(db, snapshot)
        evidence = facts(feeds, collisions)
        db.execute("INSERT OR IGNORE INTO jobs(snapshot,revision,mode,evidence) VALUES (?,?,?,?)",
                   (snapshot, revision, mode, encode(evidence)))
        return db.execute("SELECT id FROM jobs WHERE snapshot=? AND revision=? AND mode=?",
                          (snapshot, revision, mode)).fetchone()[0]


def run_one(path, chat=None, model="", context=4096, job_id=None, replay=None):
    now, token = time.time(), secrets.token_hex(16)
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("""SELECT * FROM jobs WHERE (status='queued' OR (status='running' AND expires<?))
            AND (? IS NULL OR id=?) AND (mode='baseline' OR (mode='live' AND ?) OR (mode='replay' AND ?))
            ORDER BY id LIMIT 1""", (now, job_id, job_id, chat is not None, replay is not None)).fetchone()
        if not job:
            return None
        db.execute("UPDATE jobs SET status='running', lease=?, expires=? WHERE id=?", (token, now + 210, job["id"]))
    step = 0

    def record(detail):
        nonlocal step
        step += 1
        with database(path) as db:
            db.execute("""INSERT INTO steps SELECT id,?,?,? FROM jobs WHERE id=? AND lease=? AND expires>?""",
                       (token, step, encode(detail), job["id"], token, time.time()))

    started = time.monotonic()
    evidence = json.loads(job["evidence"])
    if job["mode"] == "baseline":
        record({"tool": "ledger_evidence", "result": evidence[1]})
        record({"tool": "bank_evidence", "result": evidence[2]})
        result = {"finding": baseline(*evidence), "outcome": "rules", "input_tokens": 0, "output_tokens": 0}
    elif job["mode"] == "live":
        result = investigate(evidence, chat, model, context, record)
    else:
        responses = iter(replay.get(encode(evidence), []))

        def recorded_chat(payload, timeout):
            response = next(responses, None)
            if response is None:
                raise ValueError("No recorded response for this exact evidence snapshot")
            return response

        result = investigate(evidence, recorded_chat, model, context, record)
    result.update(elapsed_seconds=round(time.monotonic() - started, 4), mode=job["mode"], attempt=token)
    with database(path) as db:
        changed = db.execute("""UPDATE jobs SET status='done',result=?,lease=NULL,expires=NULL
            WHERE id=? AND lease=? AND expires>?""", (encode(result), job["id"], token, time.time())).rowcount
    return result if changed else None


def review(path, job_id, action, request_key, actor):
    if action not in {"accept", "reject", "request"} or not request_key or len(request_key) > 100:
        raise ValueError("Invalid reviewer action")
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute("SELECT * FROM reviews WHERE request_key=?", (request_key,)).fetchone()
        if exists:
            if (exists["job"], exists["action"], exists["actor"]) != (job_id, action, actor):
                raise ValueError("Review request key already used")
            return exists["id"]
        job = db.execute("SELECT * FROM jobs WHERE id=? AND status='done'", (job_id,)).fetchone()
        if job is None:
            raise ValueError("Finding is unavailable")
        snap, _, _, revision = current_case(db, job["snapshot"])
        latest = db.execute("SELECT MAX(version) FROM snapshots WHERE account=? AND case_id=?",
                            (snap["account"], snap["case_id"])).fetchone()[0]
        if latest != snap["version"] or revision != job["revision"]:
            raise ValueError("Evidence has changed. Refresh the case before reviewing.")
        existing = db.execute("SELECT id FROM reviews WHERE job=?", (job_id,)).fetchone()
        if existing:
            raise ValueError("Finding already reviewed")
        result = json.loads(job["result"])
        reason = result["finding"]["reason"]
        if action == "accept" and (reason in REQUESTS | {"reconciled"} or result["outcome"] not in {"verified", "rules"}):
            raise ValueError("This finding requires more evidence or has already reconciled")
        row = db.execute("INSERT INTO reviews(job,request_key,action,actor) VALUES (?,?,?,?)",
                         (job_id, request_key, action, actor))
        if action in {"accept", "request"}:
            text = NEXT.get(reason, NEXT["incomplete_evidence"]) if action == "accept" or reason in REQUESTS else NEXT["incomplete_evidence"]
            db.execute("INSERT INTO followups(review,instruction) VALUES (?,?)", (row.lastrowid, text))
        return row.lastrowid


def recheck(path):
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        for head in heads(db):
            row, feeds, collisions, _ = current_case(db, head["id"])
            if baseline(*facts(feeds, collisions))["reason"] == "reconciled":
                db.execute("""UPDATE followups SET completed_snapshot=? WHERE completed_snapshot IS NULL AND review IN (
                    SELECT r.id FROM reviews r JOIN jobs j ON j.id=r.job JOIN snapshots s ON s.id=j.snapshot
                    WHERE s.account=? AND s.case_id=? AND s.version<?)""",
                           (row["id"], row["account"], row["case_id"], row["version"]))


def cases(path):
    with database(path) as db:
        return [case_detail(db, row["id"]) for row in heads(db)]


def case_detail(db, snapshot):
    row, feeds, collisions, revision = current_case(db, snapshot)
    evidence = facts(feeds, collisions)
    match = baseline(*evidence)["reason"] == "reconciled"
    job = db.execute("""SELECT j.*,r.action,r.actor FROM jobs j LEFT JOIN reviews r ON r.job=j.id
        WHERE snapshot=? AND revision=? ORDER BY j.id DESC LIMIT 1""", (snapshot, revision)).fetchone()
    state = "reconciled" if match else "open"
    if job and not match:
        state = {"accept": "explained", "request": "evidence requested"}.get(job["action"], "open")
    return {"snapshot": snapshot, "account": row["account"], "case_id": row["case_id"], "version": row["version"],
            "revision": revision, "summary": evidence[0], "state": state, "job": dict(job) if job else None}
