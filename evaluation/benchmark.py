"""Paired evaluation with per-case checkpoints tied to the runtime and fixture digests."""

import json
import math
import os
import statistics
import subprocess
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from evaluation.datasets import COUNTS, export, generated, holdout
from settlement.model import PIN, api, probe
from settlement.service import queue, run_one
from settlement.storage import database, digest, encode, import_batch


FIELDS = ("reason", "comparison", "cents", "evidence_ids")
AMBIGUOUS = {"ambiguous", "compound"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    os.replace(stream.name, path)


def configuration(root, cases):
    paths = [root / "settlement" / name for name in ("storage.py", "service.py", "agent.py", "analysis.py", "reconcile.py", "support.py")] + [
        root / "evaluation" / name for name in ("benchmark.py", "datasets.py", "fixtures.py")]
    paths.extend((root / folder / name) for folder, name in (("settlement", "model.py"), ("config", "model-lock.json")))
    implementation = {str(p.relative_to(root).as_posix()): digest(p.read_bytes()) for p in sorted(paths)}
    config = {"implementation": implementation, "dataset_sha256": digest(encode(cases).encode()),
              "model_digest": PIN["model_digest"], "runtime": PIN["runtime_version"], "context": PIN["context"]}
    return config, digest(encode(config).encode())


def track_gpu(stop, samples):
    while not stop.is_set():
        try:
            query = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                   capture_output=True, text=True, timeout=10, check=True)
            samples.extend(int(line) for line in query.stdout.splitlines() if line.strip().isdigit())
        except (OSError, subprocess.SubprocessError):
            return
        stop.wait(1)


def run(root, development=False):
    cases = list(generated(43000, dict.fromkeys(COUNTS, 1)) if development else holdout())
    config, fingerprint = configuration(root, cases)
    release = root / ("artifacts/development-benchmark" if development else "results/benchmarks")
    cache = root / "artifacts" / "benchmark" / fingerprint
    manifest = release / "evaluation-v2-manifest.json"
    if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8"))["dataset_sha256"] != config["dataset_sha256"]:
        raise ValueError("The frozen dataset digest changed")
    if not manifest.exists():
        write_json(manifest, {"dataset_sha256": config["dataset_sha256"], "cases": len(cases),
                              "composition": dict(Counter(row[1] for row in cases)),
                              "repetitions": 1 if development else 3, "repeat_cases": 0 if development else 12})
    measurement = probe()
    stop, samples = threading.Event(), []
    sampler = threading.Thread(target=track_gpu, args=(stop, samples), daemon=True)
    sampler.start()
    rows = []
    try:
        for index, (case, kind, feeds, expected) in enumerate(cases):
            for repetition in range(3 if not development and index < 12 else 1):
                checkpoint = cache / f"{case}-{repetition}.json"
                if checkpoint.exists():
                    rows.append(json.loads(checkpoint.read_text(encoding="utf-8")))
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    db = folder / "case.db"
                    snapshot = import_batch(db, export(folder, feeds))["snapshot"]
                    results = {}
                    for mode in ("baseline", "live"):
                        job = queue(db, snapshot, mode)
                        result = run_one(db, lambda payload, timeout: api("/api/chat", payload, timeout),
                                         PIN["model"], PIN["context"], job)
                        found = {key: result["finding"][key] for key in FIELDS}
                        attempt = result.pop("attempt")
                        if mode == "live":
                            with database(db) as conn:
                                result["trace"] = [json.loads(step["detail"]) for step in conn.execute(
                                    "SELECT detail FROM steps WHERE job=? AND attempt=? ORDER BY sequence", (job, attempt))]
                        results[mode] = {**result, "correct": found == expected and result["outcome"] in {"matched", "rules", "verified"}}
                    row = {"case_id": case, "scenario": kind, "repetition": repetition, "expected": expected, **results}
                    write_json(checkpoint, row)
                    rows.append(row)
            if index % 10 == 9:
                print(f"Benchmark: {index + 1}/{len(cases)} source bundles", flush=True)
    finally:
        stop.set()
        sampler.join(timeout=11)
    primary = [r for r in rows if r["repetition"] == 0]
    groups = {
        "resolvable": [r for r in primary if r["scenario"] in {"refund", "fee", "amount", "offset"}],
        "underdetermined": [r for r in primary if r["scenario"] in AMBIGUOUS | {"late"}],
        "clean": [r for r in primary if r["scenario"] == "clean"],
    }
    timings = sorted(r["live"]["elapsed_seconds"] for r in primary if r["scenario"] != "clean")
    counts = {group: {"cases": len(records),
                      **{mode: sum(r[mode]["correct"] for r in records) for mode in ("baseline", "live")}}
              for group, records in groups.items()}
    unsafe = sum(r["live"]["finding"]["reason"] not in
                 {"await_bank_feed", "request_bank_trace", "incomplete_evidence", "missing_bank_credit"}
                 for r in groups["underdetermined"])
    summary = {"configuration": config, "run_key": fingerprint, "finished_utc": datetime.now(timezone.utc).isoformat(),
               "counts": counts, "false_resolutions": unsafe, "gpu": measurement,
               "sampled_device_peak_mib_this_invocation": max(samples, default=None),
               "median_seconds": statistics.median(timings), "p95_seconds": timings[math.ceil(.95 * len(timings)) - 1],
               "tokens": {key: sum(r["live"][key] for r in primary) for key in ("input_tokens", "output_tokens")},
               "repeated": {"additional_runs": len(rows) - len(primary), "correct": sum(r["live"]["correct"] for r in rows if r["repetition"])},
               "by_scenario": {kind: {mode: sum(r[mode]["correct"] for r in primary if r["scenario"] == kind)
                                      for mode in ("baseline", "live")} for kind in sorted({r["scenario"] for r in primary})},
               "api_charges_usd": 0}
    write_json(cache / "results.json", rows)
    write_json(release / "benchmark-v2.json", summary)
    write_json(release / "benchmark-v2-cases.json", rows)
    return {"passed": unsafe == 0, **summary}
