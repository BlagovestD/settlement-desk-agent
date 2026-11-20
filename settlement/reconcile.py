"""Reference calculations for one scoped payout in the three development scenarios."""


def net(row):
    return row["gross"] - row["fee"]


def compare(merchant, processor, bank):
    scope = ("case_id", "account", "currency", "cutoff")
    if any(merchant[k] != feed[k] for feed in (processor, bank) for k in scope):
        raise ValueError("Source scopes differ")
    if merchant["currency"] != "USD":
        raise ValueError("Only USD fixtures are supported")
    entries = {r["id"]: r for r in merchant["entries"]}
    items = {r["id"]: r for r in processor["items"]}
    if len(entries) != len(merchant["entries"]) or len(items) != len(processor["items"]):
        raise ValueError("Repeated event identity in a snapshot")
    total = sum(map(net, items.values()))
    payout = processor["header_cents"]
    delta = total - sum(map(net, entries.values()))
    credits = bank["credits"]
    matches = [c for c in credits if processor["trace"] and c["trace"] == processor["trace"]]
    posted = matches[0]["cents"] - payout if len(matches) == 1 else None
    result = {"disposition": "open", "merchant_to_processor_cents": delta,
              "components_to_header_cents": payout - total, "processor_to_bank_cents": posted, "evidence_ids": []}
    if not all(feed["complete"] for feed in (merchant, processor, bank)) or payout != total:
        return result
    if len(matches) != 1:
        candidates = matches or [c for c in credits if c["trace"] is None and c["cents"] == payout]
        if candidates:
            result.update(disposition="request_bank_trace", evidence_ids=sorted(c["id"] for c in candidates))
        return result
    if posted != 0:
        return result
    if entries == items:
        result.update(disposition="reconciled", evidence_ids=[matches[0]["id"]])
        return result
    missing = items.keys() - entries.keys()
    if len(missing) != 1 or any(items.get(k) != row for k, row in entries.items()):
        return result
    row = items[missing.pop()]
    if row["kind"] != "refund" or delta >= 0 or net(row) != delta:
        return result
    charge = entries.get(row["charge_id"])
    receipts = [r for r in merchant["refund_receipts"]
                if r["refund_id"] == row["id"] and r["cents"] == delta and r["charge_id"] == row["charge_id"]]
    if charge and charge["kind"] == "charge" and len(receipts) == 1:
        result.update(disposition="missing_ledger_refund",
                      evidence_ids=sorted([row["id"], receipts[0]["id"], charge["id"]]))
    return result
