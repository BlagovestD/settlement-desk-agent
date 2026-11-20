"""Capture live model responses for the key-free demo."""

import json
import tempfile
from pathlib import Path

from evaluation.benchmark import write_json
from evaluation.datasets import demo_bundles
from settlement.model import PIN, api, probe
from settlement.service import queue, run_one
from settlement.storage import database, import_batch


def capture(root):
    measurement = probe()
    recordings, findings = {}, {}
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        path = folder / "demo.db"
        bundles = demo_bundles(folder)
        for name in ("01-refund", "02-ambiguous"):
            snapshot = import_batch(path, bundles[name])["snapshot"]
            job = queue(path, snapshot, "live")
            messages = []

            def chat(payload, timeout):
                result = api("/api/chat", payload, timeout)
                messages.append(result)
                return result

            result = run_one(path, chat, PIN["model"], PIN["context"], job)
            with database(path) as db:
                evidence = db.execute("SELECT evidence FROM jobs WHERE id=?", (job,)).fetchone()[0]
            recordings[evidence] = messages
            findings[name] = result
    ok = (findings["01-refund"]["finding"]["reason"], findings["02-ambiguous"]["finding"]["reason"]) == (
        "missing_ledger_refund", "request_bank_trace")
    target = root / ("results" if ok else "artifacts") / "demo-replay.json"
    write_json(target, {"model": measurement, "recordings": recordings})
    return {"passed": ok, "findings": findings}
