import json
import tempfile
import unittest
from pathlib import Path

from evaluation.datasets import export
from evaluation.support_cases import support_cases
from settlement.service import cases, queue, recheck, review, run_one
from settlement.storage import database, import_batch
from settlement.web import detail


class SupportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "test.db"
        self.scenarios = {name: (feeds, expected) for _, name, feeds, expected in support_cases(development=True)}

    def imported(self, name):
        feeds, expected = self.scenarios[name]
        snapshot = import_batch(self.db, export(self.root / name, feeds))["snapshot"]
        return snapshot, feeds, expected

    def chat(self, feeds, trace, note_ids, audit="confirmed"):
        scope = {"case_id": feeds["merchant"]["case_id"], "cutoff": feeds["merchant"]["cutoff"]}
        responses = iter([
            {"tool_calls": [{"function": {"index": 0, "name": "support_evidence", "arguments": scope}}]},
            {"content": json.dumps({"trace": trace, "note_ids": note_ids})},
            {"content": json.dumps({"verdict": audit, "explanation": "Audit response"})},
        ])
        return lambda *_: {"message": next(responses)}

    def test_agent_proposes_an_evidenced_credit_and_review_requests_structured_reference(self):
        snapshot, feeds, expected = self.imported("retry_correction")
        rules_job = queue(self.db, snapshot, "baseline")
        self.assertEqual(run_one(self.db, job_id=rules_job)["finding"]["reason"], "request_bank_trace")
        job = queue(self.db, snapshot, "live")
        result = run_one(self.db, self.chat(feeds, expected["trace"], expected["required_notes"]), job_id=job)
        self.assertEqual(result["finding"]["proposal"]["candidate_id"], expected["candidate_id"])
        self.assertEqual({item["quote"] for item in result["finding"]["proposal"]["citations"]},
                         {note["text"] for note in feeds["support"]["notes"] if note["id"] in expected["required_notes"]})
        page, _ = detail(self.db, snapshot, "token", "live")
        self.assertIn(expected["trace"], page)
        self.assertIn(f"/evidence/{snapshot}/support/", page)
        self.assertNotIn("Accept explanation", page)
        with self.assertRaisesRegex(ValueError, "requires more evidence"):
            review(self.db, job, "accept", "accept-lead", "simulated analyst")
        review(self.db, job, "request", "request-structured-trace", "simulated analyst")
        case = cases(self.db)[0]
        self.assertEqual(case["state"], "evidence requested")
        self.assertIsNone(case["summary"]["differences"]["bank"])
        feeds["processor"]["trace"] = expected["trace"]
        import_batch(self.db, export(self.root / "corrected", feeds, version=2))
        recheck(self.db)
        self.assertEqual(cases(self.db)[0]["state"], "reconciled")

    def test_explicit_confirmation_also_works_with_rules(self):
        snapshot, _, expected = self.imported("explicit_reference")
        result = run_one(self.db, job_id=queue(self.db, snapshot, "baseline"))
        self.assertEqual(result["finding"]["proposal"]["trace"], expected["trace"])

    def test_audit_can_reject_a_literal_reference(self):
        snapshot, feeds, _ = self.imported("alternatives_pending")
        trace = feeds["bank"]["credits"][0]["trace"]
        notes = [row["id"] for row in feeds["support"]["notes"]]
        result = run_one(self.db, self.chat(feeds, trace, notes, audit="insufficient"),
                         job_id=queue(self.db, snapshot, "live"))
        self.assertEqual((result["outcome"], result["finding"]["reason"]), ("abstained", "request_bank_trace"))

    def test_note_after_cutoff_cannot_be_cited(self):
        snapshot, feeds, _ = self.imported("correction_after_cutoff")
        late = feeds["support"]["notes"][-1]
        job = queue(self.db, snapshot, "live")
        result = run_one(self.db, self.chat(feeds, feeds["bank"]["credits"][1]["trace"], [late["id"]]), job_id=job)
        self.assertEqual(result["outcome"], "rejected")
        with database(self.db) as db:
            job_evidence = db.execute("SELECT evidence FROM jobs WHERE id=?", (job,)).fetchone()[0]
            self.assertNotIn(late["id"], job_evidence)
            source = db.execute("SELECT 1 FROM source_rows WHERE snapshot=? AND source='support' AND row_id=?",
                                (snapshot, late["id"])).fetchone()
            self.assertIsNotNone(source)

    def test_credit_claimed_in_another_case_is_unavailable_for_a_proposal(self):
        snapshot, feeds, expected = self.imported("retry_correction")
        _, _, other, _ = next(support_cases())
        other["processor"]["trace"] = expected["trace"]
        other["bank"]["credits"] = [credit for credit in feeds["bank"]["credits"] if credit["trace"] == expected["trace"]]
        import_batch(self.db, export(self.root / "other", other))
        result = run_one(self.db, self.chat(feeds, expected["trace"], expected["required_notes"]),
                         job_id=queue(self.db, snapshot, "live"))
        self.assertEqual(result["outcome"], "rejected")
        self.assertNotIn("proposal", result["finding"])
