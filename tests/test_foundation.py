import json
import tempfile
import unittest
from pathlib import Path

from evaluation.fixtures import build_case, development_cases, write_fixtures
from evaluation.canary import investigate as live_investigate
from evaluation.workflow import run_suite, score_directory
from settlement.reconcile import compare


def investigate(feeds):
    return compare(feeds["merchant"], feeds["processor"], feeds["bank"])


class FoundationTests(unittest.TestCase):
    def test_signature_refund_case_and_corrected_snapshot(self):
        _, feeds, _ = build_case(11, "missing_refund", demo=True)
        result = investigate(feeds)
        self.assertEqual(result["merchant_to_processor_cents"], -15000)
        self.assertEqual(result["processor_to_bank_cents"], 0)
        self.assertEqual(feeds["processor"]["header_cents"], 985000)
        self.assertEqual(result["disposition"], "missing_ledger_refund")
        refund = feeds["processor"]["items"][1]
        self.assertIn(refund["id"], result["evidence_ids"])
        feeds["merchant"]["entries"].append(dict(refund))
        self.assertEqual(investigate(feeds)["disposition"], "reconciled")

    def test_ambiguous_credits_have_no_asserted_bank_variance(self):
        _, feeds, _ = build_case(12, "ambiguous_bank")
        result = investigate(feeds)
        self.assertEqual(result["disposition"], "request_bank_trace")
        self.assertIsNone(result["processor_to_bank_cents"])
        self.assertEqual(set(result["evidence_ids"]), {c["id"] for c in feeds["bank"]["credits"]})

    def test_incomplete_coverage_and_unconfirmed_refund(self):
        _, feeds, _ = build_case(14, "missing_refund")
        feeds["merchant"]["complete"] = False
        self.assertEqual(investigate(feeds)["disposition"], "open")
        feeds["merchant"]["complete"] = True
        feeds["merchant"]["refund_receipts"][0]["charge_id"] = "unrelated"
        self.assertEqual(investigate(feeds)["disposition"], "open")

    def test_offsetting_errors_do_not_reconcile(self):
        _, feeds, _ = build_case(15, "clean")
        feeds["merchant"]["entries"][0]["gross"] += 5
        feeds["merchant"]["entries"][1]["gross"] -= 5
        result = investigate(feeds)
        self.assertEqual(result["merchant_to_processor_cents"], 0)
        self.assertEqual(result["disposition"], "open")

    def test_scope_must_match(self):
        _, feeds, _ = build_case(16, "clean")
        feeds["bank"]["cutoff"] = "2027-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "scope"):
            investigate(feeds)

    def test_oracle_is_external_and_score_detects_corrupted_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_fixtures(directory)
            for source in (directory / "inputs").rglob("*.json"):
                self.assertNotIn("disposition", json.loads(source.read_text()))
            keyfile = directory / "evaluator" / "answers.json"
            key = json.loads(keyfile.read_text())
            key[next(iter(key))]["merchant_to_processor_cents"] += 1
            keyfile.write_text(json.dumps(key))
            result = score_directory(directory)
            self.assertFalse(result["passed"])
            self.assertEqual(len(result["failures"]), 1)

    def test_development_data_is_reproducible_and_balanced(self):
        first = list(development_cases())
        self.assertEqual(first, list(development_cases()))
        self.assertEqual(len(set(case for case, _, _ in first)), 30)
        reasons = [truth["disposition"] for _, _, truth in first]
        self.assertEqual(sorted(reasons.count(reason) for reason in set(reasons)), [10, 10, 10])

    def test_missing_release_suite_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(run_suite(Path(tmp), "test_imports.py")["passed"])

    def test_model_cannot_choose_an_unlisted_evidence_tool(self):
        _, feeds, _ = build_case(17, "missing_refund")
        def fake_chat(payload):
            self.assertNotIn("evaluator", json.dumps(payload))
            return {"eval_count": 4, "message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "read_answer_key", "arguments": {}}}]}}
        finding, trace, tokens = live_investigate(feeds, fake_chat)
        self.assertIsNone(finding)
        self.assertEqual(len(trace), 1)
        self.assertEqual(tokens, 4)

    def test_parallel_tool_requests_obey_the_budget(self):
        _, feeds, _ = build_case(18, "ambiguous_bank")
        scope = {k: feeds["bank"][k] for k in ("case_id", "cutoff")}
        def fake_chat(_payload):
            return {"eval_count": 100, "message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "bank_records", "arguments": scope}} for _ in range(10)]}}
        finding, trace, _ = live_investigate(feeds, fake_chat)
        self.assertIsNone(finding)
        self.assertEqual(len(trace), 8)

    def test_wrong_scope_only_receives_one_repair(self):
        _, feeds, _ = build_case(21, "missing_refund")
        def fake_chat(payload):
            for message in payload["messages"]:
                if message["role"] == "tool":
                    self.assertEqual(set(json.loads(message["content"])), {"error"})
            return {"eval_count": 20, "message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "refund_records", "arguments": {"case_id": "another-case"}}}]}}
        finding, trace, _ = live_investigate(feeds, fake_chat)
        self.assertIsNone(finding)
        self.assertTrue(trace[0]["rejected"])
        self.assertEqual(len(trace), 2)
