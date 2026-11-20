"""Build a portable source and results archive after the release gate."""

import json
import os
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


def package(root, source_digest):
    runs = root / "artifacts" / "runs"
    receipt = next(iter(sorted(runs.glob("*-release-*.json"), reverse=True)), None)
    if receipt is None:
        raise ValueError("Run the live release gate before packaging")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if not payload["passed"] or payload.get("source_sha256", payload.get("harness_sha256")) != source_digest:
        raise ValueError("The current source needs a passing live release receipt")
    paths = [root / name for name in ("run.py", "README.md", ".gitignore")]
    for name in ("settlement", "evaluation", "config", "scripts", "tests", "docs", "results", "assets"):
        paths.extend(p for p in (root / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    output = root / "artifacts" / "Settlement-Desk.zip"
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = stream.name
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "settlement-desk/" + path.relative_to(root).as_posix())
        archive.write(receipt, "settlement-desk/results/benchmarks/release-verification.json")
    with ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Portable archive integrity check failed")
    os.replace(temporary, output)
    return {"passed": True, "path": str(output), "files": len(paths) + 1, "bytes": output.stat().st_size}
