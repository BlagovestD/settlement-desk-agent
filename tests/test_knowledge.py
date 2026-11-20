import json
import tempfile
import unittest
from pathlib import Path

from settlement.knowledge import Corpus, Knowledge, Scope, verify


def document(workspace, version, effective, published, amount, market="us"):
    return {
        "document": "fees", "title": "Bank fees", "workspace": workspace, "market": market,
        "version": version, "effective": effective, "published": published,
        "passages": [
            {"anchor": "trace", "section": "Exceptions", "label": "Payout trace fee", "text": "Obtain a bank trace.", "usd_cents": amount},
            {"anchor": "retry", "section": "Exceptions", "label": "Payout retry fee", "text": "Retry a rejected payment.", "usd_cents": 125},
            {"anchor": "notes", "section": "Procedures", "label": "Bank contact", "text": "Contact the bank for details."},
        ],
    }


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "library.jsonl"
        self.rows = [
            document("atlas", "v1", "2025-01-01", "2024-12-15", 350),
            document("atlas", "v2", "2025-03-01", "2025-02-15", 450),
            document("atlas", "v3", "2025-03-01", "2025-04-15", 500),
            document("atlas", "v4", "2025-06-01", "2025-04-30", 550),
            document("cedar", "v9", "2025-03-01", "2025-02-15", 999),
            document("atlas", "v9", "2025-03-01", "2025-02-15", 888, market="ca"),
        ]

    def corpus(self):
        self.path.write_text("\n".join(map(json.dumps, self.rows)), encoding="utf-8")
        return Corpus(self.path)

    def test_search_filters_scope_and_revision_before_ranking(self):
        library = Knowledge(self.corpus())
        for known, version, amount in (("2025-03-21", "v2", 450), ("2025-05-01", "v3", 500)):
            with self.subTest(known=known):
                result = library.answer(Scope("atlas", "us", "2025-03-21", known), "Payout trace fee")
                self.assertEqual(result["status"], "search")
                self.assertTrue(all(hit.id.startswith(f"atlas/us/fees/{version}#") for hit in result["hits"]))
                self.assertEqual(result["hits"][0].cents, amount)
        early = library.answer(Scope("atlas", "us", "2025-03-21", "2024-12-14"), "Payout trace fee")
        self.assertEqual(early["hits"], [])

    def test_verifier_rejects_nonretrieved_or_out_of_scope_and_sums_only_fee_rows(self):
        corpus = self.corpus()
        hits = list(corpus.visible(Scope("atlas", "us", "2025-03-21", "2025-03-21")))
        visible = {hit.id for hit in hits}
        combined = verify([1, 2], hits, visible, True)
        self.assertEqual(combined["total_cents"], 575)
        self.assertEqual(combined["citations"], hits[:2])
        self.assertEqual(verify([1, 3], hits, visible, True)["status"], "abstained")
        self.assertEqual(verify([True, 2], hits, visible, True)["status"], "abstained")
        self.assertEqual(verify([2, 2], hits, visible, True)["status"], "abstained")
        self.assertEqual(verify([4], hits, visible, False)["status"], "abstained")
        self.assertEqual(verify([1], hits, visible - {hits[0].id}, False)["status"], "abstained")
        self.assertEqual(verify([1], hits, visible, False, "How much is a Saturday bank trace?")["status"], "abstained")
        self.assertEqual(verify([2], hits, visible, False, "What is the payout retry fee for R07?")["status"], "abstained")

    def test_import_rejects_float_fee_and_ambiguous_revisions(self):
        self.rows.append(document("ridge", "v1", "2025-01-01", "2025-01-01", 12.5))
        with self.assertRaisesRegex(ValueError, "integer cents"):
            self.corpus()
        self.rows[-1] = document("atlas", "v5", "2025-06-01", "2025-04-30", 200)
        with self.assertRaisesRegex(ValueError, "same effective and publication dates"):
            self.corpus()
