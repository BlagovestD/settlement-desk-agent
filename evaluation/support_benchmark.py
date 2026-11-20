"""Compare rules and live inference on the same ambiguous payout support threads."""

import json
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from evaluation.benchmark import configuration, write_json
from evaluation.datasets import export
from settlement.model import PIN, api, probe
from evaluation.support_cases import support_cases
from settlement.service import queue, run_one
from settlement.storage import database, digest, encode, import_batch


def run(root):
    cases = list(support_cases())
    config, _ = configuration(root, cases)
    for name in ("evaluation/support_cases.py", "evaluation/support_benchmark.py"):
        config["implementation"][name] = digest((root / name).read_bytes())
    fingerprint = digest(encode(config).encode())
    gpu = probe()
    rows = []
    for case, kind, feeds, expected in cases:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = folder / "case.db"
            snapshot = import_batch(path, export(folder, feeds))["snapshot"]
            pair = {}
            for mode in ("baseline", "live"):
                job = queue(path, snapshot, mode)
                result = run_one(path, lambda payload, timeout: api("/api/chat", payload, timeout),
                                 PIN["model"], PIN["context"], job)
                proposal = result["finding"].get("proposal")
                if expected["trace"] is None:
                    correct = result["finding"]["reason"] == "request_bank_trace" and result["outcome"] in {"rules", "abstained"}
                else:
                    correct = bool(proposal and proposal["trace"] == expected["trace"] and
                                   proposal["candidate_id"] == expected["candidate_id"] and
                                   set(expected["required_notes"]) <= {c["note_id"] for c in proposal["citations"]})
                result["correct"] = correct
                with database(path) as db:
                    result["steps"] = [json.loads(row["detail"]) for row in db.execute(
                        "SELECT detail FROM steps WHERE job=? AND attempt=? ORDER BY sequence", (job, result.pop("attempt")))]
                pair[mode] = result
            rows.append({"case_id": case, "scenario": kind, "expected": expected, **pair})
            print(f"Support benchmark {kind} | rules={pair['baseline']['correct']} live={pair['live']['correct']}", flush=True)
    resolvable = [row for row in rows if row["expected"]["trace"] is not None]
    ambiguous = [row for row in rows if row["expected"]["trace"] is None]
    counts = {
        mode: {"correct": sum(row[mode]["correct"] for row in rows),
               "correct_proposals": sum(row[mode]["correct"] for row in resolvable),
               "safe_abstentions": sum(row[mode]["correct"] for row in ambiguous),
               "wrong_proposals": sum(not row[mode]["correct"] and "proposal" in row[mode]["finding"] for row in rows),
               "median_seconds": statistics.median(row[mode]["elapsed_seconds"] for row in rows)}
        for mode in ("baseline", "live")
    }
    passed = (counts["live"]["correct_proposals"] > counts["baseline"]["correct_proposals"] and
              counts["live"]["wrong_proposals"] == 0 and counts["live"]["safe_abstentions"] == len(ambiguous))
    result = {"passed": passed, "cases": len(rows), "resolvable": len(resolvable), "ambiguous": len(ambiguous),
              "counts": counts, "configuration": config, "run_key": fingerprint, "gpu": gpu,
              "run_at": datetime.now(timezone.utc).isoformat(), "rows": rows}
    write_json(root / "results" / "benchmarks" / "support-benchmark.json", result)
    return {key: value for key, value in result.items() if key not in {"rows", "configuration"}}
