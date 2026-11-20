"""Build the development evidence and evaluator key from synthetic events."""

import copy
import json
import random
from pathlib import Path


KINDS = ("clean", "missing_refund", "ambiguous_bank")
CUTOFF = "2026-01-16T00:00:00Z"


def build_case(seed, kind, demo=False):
    if kind not in KINDS:
        raise ValueError(f"Unknown development scenario {kind}")
    rng = random.Random(seed)
    def identifier(prefix):
        return f"{prefix}_{rng.getrandbits(64):016x}"

    case, payout, trace = identifier("case"), identifier("po"), identifier("trace")
    charge, refund, receipt = identifier("ch"), identifier("re"), identifier("receipt")
    credit, other_credit = identifier("bank"), identifier("bank")
    expected = 1_000_000 if demo else rng.randrange(50_000, 5_000_001)
    fee = 3_000 if demo else rng.randrange(100, 5001)
    returned = 15_000 if demo else rng.randrange(1, expected // 3)
    charge_row = {"id": charge, "kind": "charge", "gross": expected + fee, "fee": fee}
    refund_row = {"id": refund, "kind": "refund", "charge_id": charge, "gross": -returned, "fee": 0}
    events = [charge_row, refund_row]
    merchant = {"entries": copy.deepcopy(events), "refund_receipts": [
        {"id": receipt, "refund_id": refund, "charge_id": charge, "cents": -returned}
    ]}
    paid = expected - returned
    processor = {"payout_id": payout, "trace": trace, "header_cents": paid, "items": copy.deepcopy(events)}
    bank = {"credits": [{"id": credit, "trace": trace, "cents": paid}]}
    truth = {"disposition": "reconciled", "merchant_to_processor_cents": 0,
             "components_to_header_cents": 0, "processor_to_bank_cents": 0, "evidence_ids": [credit]}
    if kind == "missing_refund":
        merchant["entries"].pop()
        truth.update(disposition="missing_ledger_refund", merchant_to_processor_cents=-returned,
                     evidence_ids=sorted([refund, receipt, charge]))
    elif kind == "ambiguous_bank":
        bank["credits"][0]["trace"] = None
        bank["credits"].append({"id": other_credit, "trace": None, "cents": paid})
        rng.shuffle(bank["credits"])
        truth.update(disposition="request_bank_trace", processor_to_bank_cents=None,
                     evidence_ids=sorted([credit, other_credit]))
    scope = {"case_id": case, "account": "demo-merchant", "currency": "USD", "cutoff": CUTOFF, "complete": True}
    feeds = {source: {**scope, **rows} for source, rows in
             (("merchant", merchant), ("processor", processor), ("bank", bank))}
    return case, feeds, truth


def development_cases():
    order = list(KINDS) * 10
    random.Random(9031).shuffle(order)
    for index, kind in enumerate(order):
        yield build_case(23000 + index, kind)


def write_fixtures(directory: Path):
    keys = {}
    for case, feeds, truth in development_cases():
        folder = directory / "inputs" / case
        folder.mkdir(parents=True)
        for name, feed in feeds.items():
            (folder / f"{name}.json").write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
        keys[case] = truth
    evaluator = directory / "evaluator"
    evaluator.mkdir()
    (evaluator / "answers.json").write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")
    return len(keys)
