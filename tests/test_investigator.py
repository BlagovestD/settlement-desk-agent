import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation.datasets import export
from evaluation.fixtures import build_case
from settlement.service import queue, run_one
from settlement.storage import database, import_batch


def response(name, arguments):
    return {"message": {"role": "assistant", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]},
            "eval_count": 5}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = self.root / "close.db"
        self.case, feeds, self.truth = build_case(19, "missing_refund", demo=True)
        self.cutoff = feeds["merchant"]["cutoff"]
        self.snapshot = import_batch(self.db, export(self.root, feeds))["snapshot"]

    def test_expired_lease_and_competing_workers(self):
        job = queue(self.db, self.snapshot, "live")
        with database(self.db) as db:
            db.execute("UPDATE jobs SET status='running', lease='expired', expires=1 WHERE id=?", (job,))
        entered, proceed = threading.Event(), threading.Event()

        def chat(payload, _timeout):
            entered.set()
            if not proceed.wait(5):
                raise TimeoutError()
            if len(payload["messages"]) == 2:
                return response("ledger_evidence", {"case_id": self.case, "cutoff": self.cutoff})
            return response("submit_finding", {"reason": "missing_ledger_refund", "evidence_ids": self.truth["evidence_ids"]})

        with ThreadPoolExecutor() as threads:
            result = threads.submit(run_one, self.db, chat)
            self.assertTrue(entered.wait(5))
            try:
                self.assertIsNone(run_one(self.db, chat))
            finally:
                proceed.set()
            self.assertEqual(result.result(10)["outcome"], "verified")
        self.assertEqual(queue(self.db, self.snapshot, "live"), job)

    def test_scope_and_invented_evidence_remain_open(self):
        queue(self.db, self.snapshot, "live")
        def chat(payload, _):
            if len(payload["messages"]) == 2:
                return response("ledger_evidence", {"case_id": "another-case", "cutoff": self.cutoff})
            self.assertEqual(set(json.loads(payload["messages"][-1]["content"])), {"error"})
            return response("submit_finding", {"reason": "missing_ledger_refund", "evidence_ids": ["unobserved"]})
        self.assertEqual(run_one(self.db, chat)["outcome"], "rejected")

    def test_timeout_and_tool_budget(self):
        queue(self.db, self.snapshot, "live")
        def timeout(*_):
            raise TimeoutError("local timeout")
        self.assertEqual(run_one(self.db, timeout)["outcome"], "model_error")
        other = import_batch(self.db, export(self.root / "next", build_case(19, "missing_refund")[1], 2))["snapshot"]
        job = queue(self.db, other, "live")
        def repeat(*_):
            return response("ledger_evidence", {"case_id": self.case, "cutoff": self.cutoff})
        self.assertEqual(run_one(self.db, repeat, job_id=job)["outcome"], "incomplete")
        with database(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM steps WHERE job=?", (job,)).fetchone()[0], 8)
