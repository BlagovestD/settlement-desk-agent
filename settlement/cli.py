"""Commands for importing settlements and running the local workbench."""

import argparse
import json
from pathlib import Path

from evaluation.datasets import demo_bundles
from settlement.model import PIN, api
from settlement.service import cases, queue, recheck, run_one
from settlement.storage import import_batch
from settlement.web import application


def local_chat(payload, timeout):
    return api("/api/chat", payload, timeout)


def prepare(db, directory):
    bundles = demo_bundles(directory)
    for name in ("01-refund", "02-ambiguous"):
        import_batch(db, bundles[name])
    return bundles


def main(argv, root):
    parser = argparse.ArgumentParser(prog="python run.py")
    parser.add_argument("command", choices=["seed", "import", "list", "investigate", "serve", "demo", "demo-live", "demo-support", "demo-rag"])
    parser.add_argument("inputs", nargs="*", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=["baseline", "live"])
    parser.add_argument("--snapshot", type=int)
    parser.add_argument("--simulated-reviewer", action="store_true")
    parser.add_argument("--knowledge-corpus", type=Path, help="Versioned UTF-8 JSONL policy library")
    args = parser.parse_args(argv)
    workspace = root / "artifacts" / "workbench"
    mode = "live" if args.command in {"demo-live", "demo-support", "demo-rag"} else args.mode or "baseline"
    filename = {"demo-live": "desk-live.db", "demo-support": "desk-support.db", "demo-rag": "desk-rag.db"}.get(args.command, "desk.db")
    db = args.db or workspace / filename
    db.parent.mkdir(parents=True, exist_ok=True)
    replay_file = root / "results" / "demo-replay.json"
    replay = None
    if args.command == "demo" and args.mode is None and replay_file.exists():
        replay = json.loads(replay_file.read_text(encoding="utf-8"))["recordings"]
        mode = "replay"
    chat = local_chat if mode == "live" else None
    if args.command in {"seed", "demo", "demo-live", "demo-rag"}:
        dirs = prepare(db, db.parent / f"{db.stem}-sample-inputs")
        print(json.dumps({name: str(path) for name, path in dirs.items()}, indent=2), flush=True)
    if args.command == "demo-support":
        from evaluation.support_cases import export_support_cases
        manifests = export_support_cases(db.parent / f"{db.stem}-sample-inputs")
        for manifest in manifests.values():
            import_batch(db, manifest)
        print(json.dumps({name: str(path) for name, path in manifests.items()}, indent=2), flush=True)
    if args.command == "import":
        if not args.inputs:
            parser.error("Provide at least one source manifest")
        results = []
        for manifest in args.inputs:
            results.append(import_batch(db, manifest))
            recheck(db)
        print(json.dumps(results, indent=2))
        return int(any(r["state"] == "quarantined" for r in results))
    if args.command == "list":
        result = [{**case, "job": case["job"]["id"] if case["job"] else None} for case in cases(db)]
        print(json.dumps(result, indent=2))
    if args.command == "investigate":
        if args.snapshot is None:
            parser.error("Provide --snapshot")
        job = queue(db, args.snapshot, mode)
        result = run_one(db, chat, PIN["model"], PIN["context"], job)
        print(json.dumps({"job": job, "result": result}, indent=2))
    if args.command in {"serve", "demo", "demo-live", "demo-support", "demo-rag"}:
        from settlement.knowledge import open_knowledge

        live_knowledge = args.command == "demo-rag" or (mode == "live" and args.knowledge_corpus is not None)
        knowledge = open_knowledge(root, live=live_knowledge, corpus_path=args.knowledge_corpus)
        actor = "simulated reviewer" if args.simulated_reviewer else "local analyst"
        server, stop, ready = application(db, args.port, mode, chat, PIN["model"], PIN["context"], actor, replay, knowledge)
        print(f"Settlement Desk http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            ready.set()
            server.server_close()
    return 0
