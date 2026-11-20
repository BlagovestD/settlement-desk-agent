"""Reproducible source bundles and private expected outcomes."""

import csv
import json
import random
from copy import deepcopy

from evaluation.fixtures import build_case
from settlement.storage import SOURCES, SUPPORT_FIELDS


def export(folder, feeds, version=1):
    folder.mkdir(parents=True, exist_ok=True)
    m, p, b = (feeds[k] for k in ("merchant", "processor", "bank"))
    rows = {
        "merchant": m["entries"] + [{"kind": "receipt", **r} for r in m["refund_receipts"]],
        "processor": [{"id": p["payout_id"], "kind": "payout", "trace": p["trace"], "cents": p["header_cents"]}, *p["items"]],
        "bank": [{"posted_at": b["cutoff"], **r} for r in b["credits"]],
    }
    manifest = {k: m[k] for k in ("account", "case_id", "currency", "cutoff")}
    manifest.update(schema=1, version=version, supersedes=version - 1 if version > 1 else None, sources={})
    sources = dict(SOURCES)
    if "support" in feeds:
        sources["support"] = SUPPORT_FIELDS
        rows["support"] = feeds["support"]["notes"]
    for source, fields in sources.items():
        filename = f"{source}.csv"
        coverage = feeds[source].get("coverage", m["cutoff"] if feeds[source]["complete"] else "2026-01-15T23:59:59Z")
        manifest["sources"][source] = {"file": filename, "coverage": coverage}
        with (folder / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows(rows[source])
    target = folder / "manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


def demo_bundles(folder):
    _, refund, _ = build_case(19, "missing_refund", demo=True)
    _, ambiguous, _ = build_case(20, "ambiguous_bank", demo=True)
    fixed_refund = deepcopy(refund)
    fixed_refund["merchant"]["entries"].append(deepcopy(refund["processor"]["items"][1]))
    fixed_bank = deepcopy(ambiguous)
    fixed_bank["bank"]["credits"][0]["trace"] = fixed_bank["processor"]["trace"]
    return {name: export(folder / name, feeds, version) for name, feeds, version in [
        ("01-refund", refund, 1), ("02-ambiguous", ambiguous, 1),
        ("03-corrected-ledger", fixed_refund, 2), ("04-bank-trace", fixed_bank, 2)]}


COUNTS = {"clean": 18, "refund": 20, "fee": 18, "offset": 16, "amount": 12,
          "ambiguous": 20, "late": 18, "compound": 10}


def generated(start_seed, counts):
    kinds = [kind for kind, n in counts.items() for _ in range(n)]
    random.Random(start_seed).shuffle(kinds)
    for i, kind in enumerate(kinds):
        seed = start_seed + i
        rng = random.Random(seed)
        base = {"refund": "missing_refund", "ambiguous": "ambiguous_bank", "compound": "ambiguous_bank"}.get(kind, "clean")
        case, feeds, truth = build_case(seed, base)
        merchant, processor, bank = (feeds[k] for k in ("merchant", "processor", "bank"))
        posted = processor["header_cents"]
        charge, refund = merchant["entries"][0], processor["items"][1]
        reason, side, amount, ids = truth["disposition"], "none", 0, truth["evidence_ids"]
        if kind == "refund":
            side, amount = "merchant", refund["gross"]
        if kind in {"ambiguous", "compound"}:
            side, amount = "bank", None
        if kind == "compound":
            merchant["entries"].remove(merchant["entries"][1])
        if kind == "fee":
            bump = rng.randint(1, 71)
            charge["fee"] += bump
            reason, side, amount, ids = "fee_difference", "merchant", bump, [charge["id"]]
        if kind == "offset":
            bump = rng.randint(1, 71)
            charge["gross"] += bump
            merchant["entries"][1]["gross"] -= bump
            reason, side, amount, ids = "offsetting_differences", "merchant", 0, [charge["id"], refund["id"]]
        if kind == "amount":
            bump = rng.randint(1, 71)
            bank["credits"][0]["cents"] += bump
            reason, side, amount, ids = "bank_amount_difference", "bank", bump, [processor["payout_id"], ids[0]]
        if kind == "late":
            bank["credits"][0]["posted_at"] = "2026-01-16T00:00:01Z"
            bank["coverage"] = "2026-01-15T23:59:59Z"
            reason, side, amount, ids = "await_bank_feed", "bank", None, [processor["payout_id"]]
        if kind in {"clean", "amount", "offset"}:
            payout_count = rng.randint(1, 4)
            for extra in range(payout_count):
                event = {"id": f"e_{rng.getrandbits(64):016x}", "kind": "charge",
                         "gross": rng.randint(1000, 10000), "fee": rng.randint(1, 90)}
                merchant["entries"].append(event.copy())
                processor["items"].append(event.copy())
                processor["header_cents"] += event["gross"] - event["fee"]
            bank["credits"][0]["cents"] += processor["header_cents"] - posted
        for entries in [merchant["entries"], processor["items"], bank["credits"]]:
            rng.shuffle(entries)
        yield case, kind, feeds, {"reason": reason, "comparison": side, "cents": amount, "evidence_ids": sorted(ids)}


def holdout():
    return generated(963800, COUNTS)
