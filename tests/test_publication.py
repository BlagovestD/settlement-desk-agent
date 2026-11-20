import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from settlement.storage import digest


ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def test_media_and_report(self):
        p = ROOT / "assets"
        walkthrough = json.loads((p / "walkthrough.json").read_text(encoding="utf-8"))
        for name, expected in walkthrough["assets"].items():
            data = (p / name).read_bytes()
            self.assertEqual(digest(data), expected)
            if name.endswith(".png"):
                width, height = struct.unpack(">II", data[16:24])
                self.assertEqual((width, height), (390, 844) if "mobile" in name else (1360, 880))
            else:
                self.assertEqual(data[4:8], b"ftyp")
                self.assertGreater(len(data), 32_000)
        self.assertEqual(walkthrough["duration_seconds"], 240)
        self.assertGreater(walkthrough["scripted_browser_seconds"], 0)
        self.assertEqual(walkthrough["simulated_reviews"], [
            {"actor": "simulated reviewer", "action": "accept", "completed_snapshot": 3},
            {"actor": "simulated reviewer", "action": "request", "completed_snapshot": 4},
        ])
        live = json.loads((ROOT / "results" / "benchmarks" / "benchmark-v2.json").read_text(encoding="utf-8"))
        resume = (ROOT / "docs" / "case-study.md").read_text(encoding="utf-8")
        self.assertIn(f'{live["counts"]["resolvable"]["live"]}/{live["counts"]["resolvable"]["cases"]}', resume)
        self.assertIn(f'{live["counts"]["underdetermined"]["cases"]} underdetermined', resume)

    def test_readme_quickstart_in_fresh_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "Settlement Desk"
            checkout.mkdir()
            for name in ("settlement", "evaluation", "config"):
                shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copyfile(ROOT / "run.py", checkout / "run.py")
            (checkout / "results").mkdir()
            shutil.copyfile(ROOT / "results" / "demo-replay.json", checkout / "results" / "demo-replay.json")
            command = [sys.executable, str(checkout / "run.py"), "demo", "--port", "0"]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            app = subprocess.Popen(command, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            try:
                for line in range(10):
                    command_output = app.stdout.readline()
                    match = re.search(r"http://127\.0\.0\.1:\d+", command_output)
                    if match:
                        break
                self.assertIsNotNone(match, command_output)
                base = match[0]
                with urlopen(base, timeout=4) as response:
                    index = response.read().decode()
                self.assertIn("Recorded model replay", index)
                self.assertIn("−$150.00", index)
                with urlopen(base + "/evidence/1/merchant/2", timeout=4) as response:
                    self.assertIn("SHA-256", response.read().decode())
                with urlopen(base + "/knowledge", timeout=4) as response:
                    panel = response.read().decode()
                token = re.search(r'name="token" value="([\w-]+)"', panel)[1]
                scope = {"scope": "atlas-pay/us", "effective_on": "2025-04-12", "known_on": "2025-05-03"}
                fields = urlencode({**scope, "token": token, "question": "What is the payout trace fee?"}).encode()
                with urlopen(base + "/knowledge", data=fields, timeout=4) as response:
                    found = response.read().decode()
                self.assertIn("Search results", found)
                self.assertIn("V2 · Effective 2025-04-01", found)
                self.assertNotIn("V3 ·", found)
                for edition in ("v2", "v3"):
                    passage = f"atlas-pay/us/fee-schedule/{edition}#bank-trace"
                    target = base + "/knowledge/source?" + urlencode({**scope, "passage": passage})
                    if edition == "v2":
                        with urlopen(target, timeout=4) as response:
                            self.assertIn("Source SHA-256", response.read().decode())
                    else:
                        with self.assertRaises(HTTPError) as error:
                            urlopen(target, timeout=4)
                        self.assertEqual(error.exception.code, 404)
            finally:
                app.terminate()
                app.communicate(timeout=10)
