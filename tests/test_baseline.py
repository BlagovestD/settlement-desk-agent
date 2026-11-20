import tempfile
import unittest
from pathlib import Path

from evaluation.benchmark import FIELDS
from evaluation.datasets import COUNTS, export, generated
from evaluation.fixtures import build_case
from settlement.service import queue, run_one
from settlement.storage import import_batch


class BaselineTests(unittest.TestCase):
    def test_every_development_family_matches_generator_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / "close.db"
            for case, kind, feeds, expected in generated(58190, dict.fromkeys(COUNTS, 2)):
                with self.subTest(kind=kind):
                    snapshot = import_batch(db, export(folder / case, feeds))["snapshot"]
                    job = queue(db, snapshot, "baseline")
                    result = run_one(db, job_id=job)["finding"]
                    self.assertEqual({k: result[k] for k in FIELDS}, expected)

    def test_one_bank_credit_cannot_be_claimed_by_two_payouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / "close.db"
            _, first, _ = build_case(1, "clean")
            _, second, _ = build_case(2, "clean")
            second["processor"]["trace"] = first["processor"]["trace"]
            second["bank"]["credits"] = first["bank"]["credits"]
            before = import_batch(db, export(folder / "first", first))["snapshot"]
            old_job = queue(db, before, "baseline")
            after = import_batch(db, export(folder / "second", second))["snapshot"]
            self.assertEqual(run_one(db, job_id=old_job)["finding"]["reason"], "reconciled")
            for snapshot in (before, after):
                job = queue(db, snapshot, "baseline")
                self.assertEqual(run_one(db, job_id=job)["finding"]["reason"], "request_bank_trace")

    def test_partial_explanation_preserves_residual(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / "close.db"
            _, feeds, _ = build_case(19, "missing_refund", demo=True)
            feeds["merchant"]["entries"][0]["fee"] += 5
            snapshot = import_batch(db, export(folder, feeds))["snapshot"]
            result = run_one(db, job_id=queue(db, snapshot, "baseline"))["finding"]
            self.assertEqual((result["reason"], result["residual_cents"]), ("incomplete_evidence", -14995))
