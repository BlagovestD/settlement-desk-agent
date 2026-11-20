import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from evaluation.datasets import demo_bundles, export
from evaluation.fixtures import build_case
from settlement.service import cases
from settlement.storage import database, import_batch


ROOT = Path(__file__).resolve().parents[1]


class ImportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = self.root / "close.db"
        self.bundles = demo_bundles(self.root)

    def cli_import(self, path, expected=0):
        run = subprocess.run([sys.executable, str(ROOT / "run.py"), "import", str(path), "--db", str(self.db)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
        return json.loads(run.stdout)[0] if run.stdout else run.stderr

    def test_import_duplicate_conflict_bad_amount_and_correction(self):
        refund = self.bundles["01-refund"]
        first = self.cli_import(refund)
        self.assertEqual(first["state"], "imported")
        self.assertEqual(self.cli_import(refund), {**first, "state": "duplicate"})
        with database(self.db) as db:
            def sources():
                return [tuple(row) for row in db.execute("SELECT * FROM source_rows ORDER BY snapshot,source,line")]
            provenance = sources()
            source = refund.with_name("merchant.csv")
            data = source.read_text()
            source.write_text(data.replace("1003000", "1003000.5"), encoding="utf-8")
            self.assertIn("row ending at line 2", self.cli_import(refund, 1))
            source.write_text(data.replace("1003000", "1003001"), encoding="utf-8")
            self.assertEqual(self.cli_import(refund, 1)["state"], "quarantined")
            self.assertEqual(sources(), provenance)
        self.assertEqual(cases(self.db)[0]["summary"]["differences"], {"merchant": -15000, "processor": 0, "bank": 0})
        self.cli_import(self.bundles["03-corrected-ledger"])
        self.assertEqual(cases(self.db)[0]["state"], "reconciled")

    def test_out_of_order_versions_and_account_namespaces(self):
        current = import_batch(self.db, self.bundles["03-corrected-ledger"])
        import_batch(self.db, self.bundles["01-refund"])
        self.assertEqual(cases(self.db)[0]["snapshot"], current["snapshot"])
        _, feeds, _ = build_case(19, "missing_refund")
        for feed in feeds.values():
            feed["account"] = "second-merchant"
        other = import_batch(self.db, export(self.root / "other", feeds))
        self.assertNotEqual(other["snapshot"], current["snapshot"])
        self.assertEqual(len(cases(self.db)), 2)

    def test_future_credit_stays_in_provenance_without_counting_as_posted(self):
        _, feeds, _ = build_case(1, "clean")
        feeds["bank"]["credits"][0]["posted_at"] = "2026-01-16T00:00:01Z"
        result = import_batch(self.db, export(self.root / "future", feeds))
        self.assertIsNone(cases(self.db)[0]["summary"]["bank_credit"])
        with database(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM source_rows WHERE snapshot=? AND source='bank'",
                                        (result["snapshot"],)).fetchone()[0], 1)
