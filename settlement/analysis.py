"""Exact comparisons and evidence-backed hypotheses about one settlement."""

from settlement.reconcile import net
from settlement.support import rules_proposal


NEXT = {
    "support_trace_proposal": "Review the support notes and request a structured processor reference before reconciling.",
    "missing_ledger_refund": "Book the confirmed refund in the merchant ledger and reimport.",
    "fee_difference": "Check the fee schedule and correct the merchant expectation.",
    "offsetting_differences": "Review both line differences before closing the payout.",
    "request_bank_trace": "Obtain a trace reference that identifies the bank credit.",
    "await_bank_feed": "Recheck after the bank statement covers the cutoff.",
    "missing_bank_credit": "Request a bank posting or investigate a delayed deposit.",
    "bank_amount_difference": "Request evidence explaining the bank amount difference.",
    "incomplete_evidence": "Obtain complete source records and rerun reconciliation.",
}
REQUESTS = {"request_bank_trace", "await_bank_feed", "missing_bank_credit", "incomplete_evidence",
            "support_trace_proposal"}


def facts(feeds, collisions=()):
    merchant, processor, bank = (feeds[name] for name in ("merchant", "processor", "bank"))
    entries = {row["id"]: row for row in merchant["entries"]}
    items = {row["id"]: row for row in processor["items"]}
    changed = sorted(key for key in entries.keys() | items.keys() if entries.get(key) != items.get(key))
    payout = processor["header_cents"]
    merchant_net, component_net = (sum(map(net, rows.values())) for rows in (entries, items))
    trace = processor["trace"]
    exact = [row for row in bank["credits"] if trace and row["trace"] == trace]
    matched = exact[0] if len(exact) == 1 and exact[0]["id"] not in collisions else None
    hints = exact or [row for row in bank["credits"] if row["trace"] is None and row["cents"] == payout]
    if "support" in feeds and not trace:
        hints = [row for row in bank["credits"] if row["cents"] == payout]
    comparisons = {"merchant": component_net - merchant_net, "processor": payout - component_net,
                   "bank": matched["cents"] - payout if matched else None}
    summary = {"account": merchant["account"], "case": merchant["case_id"], "cutoff": merchant["cutoff"],
               "merchant_net": merchant_net, "component_net": component_net, "payout": payout,
               "bank_credit": matched["cents"] if matched else None, "differences": comparisons,
               "changed_lines": len(changed), "coverage": {name: feeds[name]["complete"] for name in ("merchant", "processor", "bank")}}
    # Tool payloads contain observations, including a scoped absence. They have no verdict.
    ledger = {"differences": [{"event": key, "merchant": entries.get(key), "processor": items.get(key)}
                             for key in changed],
              "receipts": [row for row in merchant["refund_receipts"] if row["refund_id"] in changed],
              "referenced_charges": [entries[key] for key in sorted({items[k].get("charge_id") for k in changed if k in items} - {None})
                                     if key in entries],
              "merchant_complete": merchant["complete"], "processor_complete": processor["complete"]}
    for line in ledger["differences"]:
        expected, actual = line["merchant"], line["processor"]
        line["net_difference_cents"] = (net(actual) if actual else 0) - (net(expected) if expected else 0)
        line["changed_fields"] = ([key for key in sorted(expected.keys() | actual.keys()) if expected.get(key) != actual.get(key)]
                                  if expected and actual else ["missing_merchant" if actual else "missing_processor"])
    bank_view = {"payout_id": processor["payout_id"], "payout_trace": trace,
                 "bank_complete": bank["complete"], "watermark": bank["coverage"],
                 "candidates": hints, "claimed_elsewhere": list(collisions)}
    if (not trace and posted_unknown(comparisons, bank_view, ledger, changed)
            and feeds.get("support", {}).get("complete") and feeds["support"]["notes"]):
        bank_view["support_notes"] = feeds["support"]["notes"]
    return summary, ledger, bank_view


def posted_unknown(comparisons, bank, ledger, changed):
    return (comparisons["bank"] is None and not comparisons["processor"] and not changed and
            bank["bank_complete"] and ledger["merchant_complete"] and ledger["processor_complete"] and
            any(row["trace"] for row in bank["candidates"]))


def finding(reason, side, amount, ids=(), residual=0):
    return {"reason": reason, "comparison": side, "cents": amount, "evidence_ids": sorted(set(ids)),
            "residual_cents": residual, "next_action": NEXT.get(reason, "")}


def baseline(summary, ledger, bank):
    merchant, processor, posted = (summary["differences"][side] for side in ("merchant", "processor", "bank"))
    changes = ledger["differences"]
    candidates = [row["id"] for row in bank["candidates"]]
    payout = bank["payout_id"]
    if not ledger["merchant_complete"] or not ledger["processor_complete"] or processor:
        return finding("incomplete_evidence", "processor", processor, [payout], residual=processor)
    if not bank["bank_complete"]:
        return finding("await_bank_feed", "bank", posted, [payout], residual=posted)
    if posted is None:
        proposal = rules_proposal(bank)
        if proposal is not None:
            return support_finding(proposal)
        reason = "request_bank_trace" if candidates else "missing_bank_credit"
        return finding(reason, "bank", None, candidates or [payout], residual=None)
    if posted:
        return finding("bank_amount_difference", "bank", posted, [payout, *candidates])
    if not changes:
        return finding("reconciled", "none", 0, candidates)
    if len(changes) == 1:
        change = changes[0]
        expected, actual = change["merchant"], change["processor"]
        if expected is None and actual and actual["kind"] == "refund" and net(actual) == merchant:
            charge = actual["charge_id"]
            receipts = [r for r in ledger["receipts"] if r["refund_id"] == actual["id"]
                        and r["charge_id"] == charge and r["cents"] == merchant]
            if len(receipts) == 1 and any(r["id"] == charge and r["kind"] == "charge" for r in ledger["referenced_charges"]):
                return finding("missing_ledger_refund", "merchant", merchant, [actual["id"], charge, receipts[0]["id"]])
        if expected and actual and expected["fee"] != actual["fee"] and (
            {**expected, "fee": actual["fee"]} == actual
        ):
            return finding("fee_difference", "merchant", merchant, [change["event"]])
    if merchant == 0 and all(row["merchant"] and row["processor"] for row in changes):
        return finding("offsetting_differences", "merchant", 0, [row["event"] for row in changes])
    return finding("incomplete_evidence", "merchant", merchant, [row["event"] for row in changes], residual=merchant)


def support_finding(proposal):
    notes = proposal["citations"]
    return {**finding("support_trace_proposal", "bank", None,
                      [proposal["candidate_id"], *(citation["note_id"] for citation in notes)], residual=None),
            "proposal": proposal}
