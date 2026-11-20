"""Settlement Desk project commands. Run from any working directory."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from settlement.model import probe


ROOT = Path(__file__).resolve().parent


def source_digest():
    paths = [Path("run.py")]
    for folder, pattern in [("evaluation", "*.py"), ("config", "*.json"), ("settlement", "*.py"), ("settlement", "*.css"),
                            ("settlement", "*.jsonl"),
                            ("tests", "*.py"), ("scripts", "*")]:
        paths.extend(p.relative_to(ROOT) for p in (ROOT / folder).rglob(pattern)
                     if p.is_file() and "__pycache__" not in p.parts)
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.as_posix().encode() + b"\0")
        digest.update(hashlib.sha256((ROOT / path).read_bytes()).digest())
    return digest.hexdigest()


def report(command, result):
    folder = ROOT / "artifacts" / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {"command": command, "finished_utc": now.isoformat(), "source_sha256": source_digest(), **result}
    data = json.dumps(payload, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as tmp:
        tmp.write(data)
    target = folder / f"{now:%Y%m%dT%H%M%S%fZ}-{command}-{os.getpid()}.json"
    os.replace(tmp.name, target)
    if command in {"benchmark-rag", "benchmark-rag-dev"} and "results" in payload:
        if command == "benchmark-rag" and payload["passed"]:
            from evaluation.knowledge_benchmark import publish

            publish(ROOT, payload)
        print(json.dumps({key: value for key, value in payload.items() if key != "results"}, indent=2))
    else:
        print(data, end="")
    print(f"Saved {target}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in {"seed", "import", "list", "investigate", "serve", "demo", "demo-live", "demo-support", "demo-rag"}:
        from settlement.cli import main as workbench
        try:
            return workbench(sys.argv[1:], ROOT)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "check", "probe", "canary", "benchmark", "benchmark-dev",
                                           "benchmark-support", "support-fixtures", "benchmark-rag", "benchmark-rag-dev",
                                           "record-demo", "walkthrough", "release", "package", "status", "fixtures"])
    args = parser.parse_args()
    try:
        if args.command == "probe":
            result = {"passed": True, "measurement": probe()}
        elif args.command == "canary":
            from evaluation.canary import canaries
            measurement = probe()
            result = {**canaries(), "measurement": measurement}
        elif args.command in {"benchmark", "benchmark-dev"}:
            from evaluation.benchmark import run
            result = run(ROOT, development=args.command == "benchmark-dev")
        elif args.command == "benchmark-support":
            from evaluation.support_benchmark import run
            result = run(ROOT)
        elif args.command in {"benchmark-rag", "benchmark-rag-dev"}:
            from evaluation.knowledge_benchmark import run
            result = run(ROOT, development=args.command == "benchmark-rag-dev")
        elif args.command == "support-fixtures":
            from evaluation.support_cases import export_support_cases
            base = ROOT / "artifacts"
            base.mkdir(exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="support-", dir=base))
            result = {"passed": True, "manifests": {key: str(path) for key, path in export_support_cases(folder).items()}}
        elif args.command == "record-demo":
            from scripts.record_demo import capture
            result = capture(ROOT)
        elif args.command == "walkthrough":
            from scripts.walkthrough import produce
            result = produce(ROOT)
        elif args.command == "package":
            from scripts.package import package
            result = package(ROOT, source_digest())
        else:
            from evaluation.workflow import execute
            result = execute(args.command, ROOT)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        result = {"passed": False, "error": str(exc)}
    report(args.command, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
