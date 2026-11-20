"""Run the browser walkthrough from a fresh app and render the captioned MP4."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from settlement.cli import prepare
from evaluation.benchmark import write_json
from settlement.storage import database, digest
from settlement.web import application


FRAMES = [
    ("01-queue", 20, "Open the daily close queue. Two synthetic payouts require review."),
    ("02-refund", 30, "The merchant expects $10,000. The processor and bank agree on $9,850. The $150 difference is upstream of the bank."),
    ("03-finding", 40, "The recorded model links the processor refund to the original charge and merchant receipt. The verifier checks the complete ledger and exact variance."),
    ("04-source", 27, "Follow a citation to the original CSV fields, line number and file digest. The original source remains available after corrections."),
    ("05-explained", 31, "The simulated reviewer accepts the explanation. The follow-up remains open until a new merchant ledger balances."),
    ("06-ambiguous", 42, "Two credits match the payout amount. The model requests a bank trace and cites the candidates. The payout remains open."),
    ("07-reconciled", 24, "Import the corrected ledger and the later bank statement through the CLI. Both payouts now reconcile."),
    ("08-history", 26, "Reopen the case. The review history shows the original decision and the completed follow-up. Earlier snapshots remain inspectable."),
]


def edge_binary():
    installed = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    default = installed / "Microsoft/Edge/Application/msedge.exe"
    return shutil.which("msedge") or str(default)


def browser_story(root, workspace, captures, mode="replay"):
    db = workspace / "activity.db"
    bundles = prepare(db, workspace / "sources")
    replay = None
    if mode == "replay":
        replay = json.loads((root / "results/demo-replay.json").read_text(encoding="utf-8"))["recordings"]
    server, stop, event = application(db, 0, mode=mode, actor="simulated reviewer", replay=replay)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    try:
        subprocess.run(["node", str(root / "scripts/browser-check.mjs"), edge_binary(), str(workspace),
                        str(captures), f"http://127.0.0.1:{server.server_port}", sys.executable, str(root / "run.py"),
                        str(db), str(bundles["01-refund"].parent.parent)], check=True, timeout=120)
        with database(db) as conn:
            reviews = [dict(row) for row in conn.execute("""SELECT r.actor,r.action,f.completed_snapshot
                FROM reviews r JOIN followups f ON f.review=r.id ORDER BY r.id""")]
        return reviews
    finally:
        stop.set()
        event.set()
        server.shutdown()
        server_thread.join()
        server.server_close()


def ffmpeg_binary(root):
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    wheel = root / ".runtime/ffmpeg-wheel.whl"
    expected = "02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a"
    if not wheel.exists() or digest(wheel.read_bytes()) != expected:
        raise ValueError("The pinned Windows FFmpeg wheel is missing or its digest differs")
    target = wheel.with_name("ffmpeg.exe")
    with zipfile.ZipFile(wheel) as archive:
        target.write_bytes(archive.read("imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"))
    return str(target)


def video(root, capture, output):
    stamp = lambda seconds: f"00:{seconds // 60:02}:{seconds % 60:02},000"
    elapsed = 0
    concat, captions = [], []
    for index, (name, duration, caption) in enumerate(FRAMES, 1):
        concat += [f"file '{name}.png'", f"duration {duration}"]
        captions += [str(index), f"{stamp(elapsed)} --> {stamp(elapsed + duration)}", caption, ""]
        elapsed += duration
    concat.append(f"file '{FRAMES[-1][0]}.png'")
    (capture / "frames.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")
    (capture / "captions.srt").write_text("\n".join(captions), encoding="utf-8")
    style = "Fontname=Segoe UI,FontSize=11,BorderStyle=3,Outline=2,MarginV=6"
    subprocess.run([ffmpeg_binary(root), "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "1",
                    "-i", "frames.txt", "-vf", f"pad=1360:1000:0:0:color=0x132f29,subtitles=captions.srt:force_style='{style}'",
                    "-t", str(elapsed), "-r", "12", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    "-preset", "fast", "-movflags", "+faststart", str(output)], cwd=capture, check=True, timeout=180)
    return elapsed


def produce(root):
    folder = root / "assets"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="settlement-browser-") as tmp:
        workspace = Path(tmp)
        frames = workspace / "frames"
        reviews = browser_story(root, workspace, frames)
        browser_seconds = round(time.monotonic() - started, 3)
        for name in ("01-queue", "03-finding", "06-ambiguous", "08-history", "09-mobile"):
            shutil.copyfile(frames / f"{name}.png", folder / f"{name}.png")
        seconds = video(root, frames, folder / "settlement-desk-walkthrough.mp4")
    assets = [folder / f"{name}.png" for name in ("01-queue", "03-finding", "06-ambiguous", "08-history", "09-mobile")]
    assets.append(folder / "settlement-desk-walkthrough.mp4")
    result = {"passed": True, "duration_seconds": seconds, "simulated_reviews": reviews,
              "scripted_browser_seconds": browser_seconds,
              "assets": {p.name: digest(p.read_bytes()) for p in assets}}
    write_json(folder / "walkthrough.json", result)
    return result
