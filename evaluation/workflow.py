import json
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.fixtures import write_fixtures
from settlement.model import PIN, probe
from settlement.reconcile import compare


# Order is the release dependency chain. Each future suite exercises public entry points.
SUITES = (
    ("imports", "test_imports.py"),
    ("baseline", "test_baseline.py"),
    ("investigator", "test_investigator.py"),
    ("support_notes", "test_support.py"),
    ("review", "test_review.py"),
    ("knowledge", "test_knowledge.py"),
    ("live_benchmark", "test_live_benchmark.py"),
    ("publication", "test_publication.py"),
)


def inventory(root):
    gpu = shutil.which("nvidia-smi")
    gpu_result = None
    if gpu:
        result = subprocess.run([gpu, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=15)
        gpu_result = (result.stdout or result.stderr).strip()
    with sqlite3.connect(":memory:") as db:
        sqlite_ok = db.execute("SELECT 1").fetchone() == (1,)
    return {"passed": sys.version_info >= (3, 11) and sqlite_ok, "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "gpu_inventory": gpu_result, "free_gib": shutil.disk_usage(root).free // 2**30,
            "pinned_runtime_cached": (root / ".runtime" / f"ollama-{PIN['runtime_version']}" / "READY").is_file(),
            "model_gate": "Use scripts/with-model.ps1 to verify model digest, inference and GPU residency."}


def run_suite(root, filename):
    folder = root / "tests"
    if not (folder / filename).is_file():
        return {"passed": False, "state": "unimplemented", "suite": filename}
    suite = unittest.defaultTestLoader.discover(str(folder), pattern=filename)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.testsRun > 0 and result.wasSuccessful() and not result.skipped and not result.expectedFailures
    return {"passed": bool(ok), "suite": filename, "tests": result.testsRun, "skipped": len(result.skipped),
            "failures": [str(t) for t, _ in result.failures + result.errors]}


def score_directory(directory):
    answers = json.loads((directory / "evaluator" / "answers.json").read_text())
    cases = sorted((directory / "inputs").iterdir())
    if not cases or {case.name for case in cases} != set(answers):
        raise ValueError("Case inventory differs from the evaluator manifest")
    errors = []
    for case in cases:
        feeds = [json.loads((case / f"{name}.json").read_text()) for name in ("merchant", "processor", "bank")]
        finding = compare(*feeds)
        if finding != answers[case.name]:
            errors.append({"case": case.name, "found": finding, "expected": answers[case.name]})
    return {"passed": not errors, "cases": len(cases), "failures": errors}


def foundation(root):
    tests = run_suite(root, "test_foundation.py")
    with tempfile.TemporaryDirectory(prefix="settlement-check-") as tmp:
        write_fixtures(Path(tmp))
        scored = score_directory(Path(tmp))
    return {"passed": tests["passed"] and scored["passed"], "tests": tests, "development_cases": scored}


def execute(command, root):
    if command == "doctor":
        return inventory(root)
    if command == "check":
        return foundation(root)
    if command == "fixtures":
        base = root / "artifacts" / "fixtures"
        base.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="dev-", dir=base))
        return {"passed": True, "cases": write_fixtures(folder), "directory": str(folder)}
    if command == "status":
        folder = root / "artifacts" / "runs"
        latest = {}
        for path in sorted(folder.glob("*.json"), reverse=True):
            run = json.loads(path.read_text(encoding="utf-8"))
            if run["command"] != "status" and run["command"] not in latest:
                latest[run["command"]] = {"passed": run["passed"], "finished_utc": run["finished_utc"],
                                          "source_sha256": run.get("source_sha256", run.get("harness_sha256")), "report": path.name}
        return {"passed": True, "latest_recorded_runs": latest,
                "remaining_suites_to_implement": [name for name, file in SUITES if not (root / "tests" / file).is_file()]}
    gates = {"foundation": foundation(root)}
    for name, file in SUITES:
        if not all(g["passed"] for g in gates.values()):
            break
        if name == "live_benchmark" and (root / "tests" / file).is_file():
            gates["gpu_probe"] = {"passed": True, "measurement": probe()}
        gates[name] = run_suite(root, file)
    return {"passed": len([name for name, _ in SUITES if name in gates]) == len(SUITES)
            and all(g["passed"] for g in gates.values()), "gates": gates,
            "remaining_gates": [name for name, _ in SUITES if not gates.get(name, {}).get("passed")]}
