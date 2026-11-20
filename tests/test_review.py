import tempfile
import unittest
from pathlib import Path

from evaluation.datasets import demo_bundles
from settlement.service import cases, queue, recheck, review, run_one
from settlement.storage import database, import_batch


class ReviewTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        folder = Path(workspace.name)
        self.path = folder / "workbench.db"
        self.bundles = demo_bundles(folder)

    def investigate(self, name):
        snapshot = import_batch(self.path, self.bundles[name])["snapshot"]
        job = queue(self.path, snapshot, "baseline")
        run_one(self.path, job_id=job)
        return job

    def test_review_is_idempotent_and_closes_on_corrected_import(self):
        job = self.investigate("01-refund")
        action = review(self.path, job, "accept", "review-1", "simulated reviewer")
        self.assertEqual(review(self.path, job, "accept", "review-1", "simulated reviewer"), action)
        self.assertEqual(cases(self.path)[0]["state"], "explained")
        snapshot = import_batch(self.path, self.bundles["03-corrected-ledger"])["snapshot"]
        recheck(self.path)
        self.assertEqual(cases(self.path)[0]["state"], "reconciled")
        with database(self.path) as db:
            self.assertEqual(db.execute("SELECT completed_snapshot FROM followups").fetchone()[0], snapshot)
            self.assertEqual(db.execute("SELECT count(*) FROM reviews").fetchone()[0], 1)

    def test_ambiguous_deposit_requires_evidence(self):
        job = self.investigate("02-ambiguous")
        with self.assertRaisesRegex(ValueError, "requires more evidence"):
            review(self.path, job, "accept", "invalid", "simulated reviewer")
        review(self.path, job, "request", "review-2", "simulated reviewer")
        self.assertEqual(cases(self.path)[0]["state"], "evidence requested")

    def test_rejected_and_stale_reviews(self):
        stale = self.investigate("01-refund")
        import_batch(self.path, self.bundles["03-corrected-ledger"])
        with self.assertRaisesRegex(ValueError, "Evidence has changed"):
            review(self.path, stale, "accept", "stale", "simulated reviewer")
        job = self.investigate("02-ambiguous")
        review(self.path, job, "reject", "review-3", "simulated reviewer")
        self.assertEqual(cases(self.path)[1]["state"], "open")
        with self.assertRaisesRegex(ValueError, "already reviewed"):
            review(self.path, job, "request", "review-4", "simulated reviewer")
