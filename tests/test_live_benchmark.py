import json
import unittest
from collections import Counter
from pathlib import Path

from evaluation.benchmark import AMBIGUOUS, FIELDS, configuration
from evaluation.datasets import holdout
from evaluation.knowledge_benchmark import implementation_digest, scenarios, sha
from evaluation.support_cases import support_cases
from settlement.storage import digest, encode


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkReceiptTests(unittest.TestCase):
    def test_knowledge_benchmark_receipt_and_scope(self):
        receipt = json.loads((ROOT / "results/benchmarks/knowledge-benchmark.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "results/benchmarks/knowledge-eval-manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(receipt["passed"])
        # The archived run predates the directory refactor. Its source fingerprint
        # authenticates that historical release, not this reorganized source tree.
        self.assertRegex(receipt["knowledge_implementation_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(implementation_digest(ROOT), r"^[0-9a-f]{64}$")
        self.assertEqual(receipt["questions_sha256"], sha(list(scenarios(False))))
        self.assertEqual({key: receipt[key] for key in manifest}, manifest)
        self.assertGreater(receipt["model_gpu_resident_bytes"][receipt["generator"]], 0)
        self.assertGreater(receipt["model_gpu_resident_bytes"][receipt["embedding_model"]], 0)
        for result in receipt["methods"].values():
            self.assertEqual(result["scope_violations"], 0)
            self.assertEqual(result.get("citation_scope_violations", 0), 0)
            self.assertEqual(result.get("invalid_outputs", 0), 0)

    def test_support_holdout_improves_recall_without_wrong_proposals(self):
        receipt = json.loads((ROOT / "results/benchmarks/support-benchmark.json").read_text(encoding="utf-8"))
        cases = list(support_cases())
        current_config, _ = configuration(ROOT, cases)
        archived_config = receipt["configuration"]
        # Preserve frozen historical implementation digests rather than claiming
        # relocated/edited source files have their original byte-for-byte hashes.
        for key in ("dataset_sha256", "model_digest", "runtime", "context"):
            self.assertEqual(archived_config[key], current_config[key])
        self.assertTrue(all(len(h) == 64 for h in archived_config["implementation"].values()))
        self.assertEqual(receipt["run_key"], digest(encode(archived_config).encode()))
        labels = {case: expected for case, _, _, expected in cases}
        self.assertEqual({row["case_id"]: row["expected"] for row in receipt["rows"]}, labels)
        self.assertTrue(receipt["passed"])
        self.assertGreater(receipt["counts"]["live"]["correct_proposals"], receipt["counts"]["baseline"]["correct_proposals"])
        self.assertEqual(receipt["counts"]["live"]["wrong_proposals"], 0)
        for row in receipt["rows"]:
            if row["expected"]["trace"] is None:
                self.assertEqual(row["live"]["outcome"], "abstained")
                self.assertNotIn("proposal", row["live"]["finding"])

    def test_frozen_model_and_paired_results(self):
        results = ROOT / "results" / "benchmarks"
        summary = json.loads((results / "benchmark-v2.json").read_text(encoding="utf-8"))
        rows = json.loads((results / "benchmark-v2-cases.json").read_text(encoding="utf-8"))
        cases = list(holdout())
        config, fingerprint = configuration(ROOT, cases)
        archived_config = summary["configuration"]
        for key in ("dataset_sha256", "model_digest", "runtime", "context"):
            self.assertEqual(archived_config[key], config[key])
        self.assertEqual(summary["run_key"], digest(encode(archived_config).encode()))
        self.assertEqual(summary["gpu"]["model_digest"], archived_config["model_digest"])
        self.assertGreater(summary["gpu"]["gpu_resident_bytes"], 0)
        primary = [row for row in rows if row["repetition"] == 0]
        labels = {r[0]: r[3] for r in cases}
        self.assertEqual({r["case_id"] for r in primary}, set(labels))
        self.assertGreaterEqual(len(primary), 120)
        self.assertEqual(len(rows) - len(primary), 24)
        self.assertEqual(Counter(r["case_id"] for r in rows).most_common(1)[0][1], 3)
        verified_count = 0
        for row in rows:
            expected = labels[row["case_id"]]
            self.assertEqual(row["expected"], expected)
            for mode in ("baseline", "live"):
                got = row[mode]
                passes = {key: got["finding"][key] for key in FIELDS} == expected
                self.assertEqual(got["correct"], passes and got["outcome"] in {"matched", "rules", "verified"})
            if row["live"]["outcome"] == "verified":
                verified_count += 1
                self.assertTrue(any(step.get("accepted") for step in row["live"]["trace"]))
                self.assertGreater(row["live"]["output_tokens"], 0)
            if row["scenario"] in AMBIGUOUS:
                self.assertIn(row["live"]["finding"]["reason"], {"request_bank_trace", "incomplete_evidence"})
        self.assertGreater(verified_count, 0)
        for label, scenarios in {"clean": {"clean"}, "resolvable": {"refund", "fee", "offset", "amount"},
                                 "underdetermined": AMBIGUOUS | {"late"}}.items():
            group = [row for row in primary if row["scenario"] in scenarios]
            self.assertEqual(summary["counts"][label], {"cases": len(group),
                **{mode: sum(row[mode]["correct"] for row in group) for mode in ("live", "baseline")}})
        self.assertEqual(summary["repeated"]["correct"], sum(r["live"]["correct"] for r in rows if r["repetition"]))
        self.assertEqual(summary["false_resolutions"], 0)
        self.assertEqual(summary["api_charges_usd"], 0)
