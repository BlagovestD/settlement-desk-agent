"""Two live development investigations with a bounded, read-only evidence interface."""

import json
import time

from evaluation.fixtures import build_case
from settlement.model import PIN, api


def tool(name, description, properties=None):
    properties = properties or {}
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}}


SCOPE = {"case_id": {"type": "string"}, "cutoff": {"type": "string", "description": "Exact UTC snapshot cutoff"}}
TOOLS = [
    tool("refund_records", "Read processor rows, merchant ledger entries, coverage and refund receipts for the case.", SCOPE),
    tool("bank_records", "Read the payout trace and posted bank credits in the candidate window.", SCOPE),
    tool("submit_finding", "Submit a finding with evidence identifiers from the tools.", {
        "disposition": {"type": "string", "enum": ["missing_ledger_refund", "request_bank_trace", "open"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    }),
]
POLICY = """Investigate the comparison that is unresolved. All amounts are integer USD cents.
merchant_delta is settlement net minus the merchant's expected net. A negative delta can be
explained by a processor refund only if that exact refund is absent from the complete merchant
ledger, the charge and receipt match, and the signed refund equals the delta. Cite exactly
three IDs for that finding: refund, charge, receipt.
A null bank_delta means the deposit is unconfirmed. Examine bank evidence. Two same-amount
credits without a matching trace require request_bank_trace. Cite only the candidate credit IDs.
When merchant_delta is zero and the same refund is present on both sides it is fully accounted
for. Use open for insufficient evidence. Finish by calling submit_finding."""


def investigate(feeds, chat):
    merchant, processor, bank = (feeds[k] for k in ("merchant", "processor", "bank"))
    def total(rows):
        return sum(row["gross"] - row["fee"] for row in rows)
    unique_matches = [c for c in bank["credits"] if processor["trace"] and c["trace"] == processor["trace"]]
    summary = {"case_id": merchant["case_id"], "cutoff": merchant["cutoff"],
               "merchant_net": total(merchant["entries"]), "settlement_net": total(processor["items"]),
               "payout_header": processor["header_cents"],
               "merchant_delta": total(processor["items"]) - total(merchant["entries"]),
               "bank_delta": unique_matches[0]["cents"] - processor["header_cents"] if len(unique_matches) == 1 else None}
    evidence = {"refund_records": {"items": processor["items"], "entries": merchant["entries"],
                                   "receipts": merchant["refund_receipts"], "complete": merchant["complete"]},
                "bank_records": {"trace": processor["trace"], "credits": bank["credits"]}}
    messages = [{"role": "system", "content": POLICY}, {"role": "user", "content": json.dumps(summary)}]
    trace = []
    tokens = 0
    repaired = False
    scope = {k: summary[k] for k in SCOPE}
    for _ in range(5):
        answer = chat({"model": PIN["model"], "stream": False, "think": False, "messages": messages, "tools": TOOLS,
                       "options": {"temperature": 0, "num_ctx": PIN["context"], "num_predict": 320}})
        tokens += answer["eval_count"]
        message = answer["message"]
        messages.append(message)
        calls = message.get("tool_calls", [])
        if not calls:
            break
        for call in calls:
            function = call["function"]
            name, args = function["name"], function["arguments"]
            if len(trace) >= 8:
                return None, trace, tokens
            trace.append({"tool": name, "arguments": args})
            if name == "submit_finding":
                return args, trace, tokens
            if name not in evidence:
                return None, trace, tokens
            if args == scope:
                content = evidence[name]
            elif not repaired:
                content = {"error": "Use exactly the case_id and cutoff from the initial case summary."}
                trace[-1]["rejected"] = True
                repaired = True
            else:
                return None, trace, tokens
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(content)})
    return None, trace, tokens


def canaries():
    results = []
    for seed, kind, required_tool in [(19, "missing_refund", "refund_records"), (20, "ambiguous_bank", "bank_records")]:
        _, feeds, truth = build_case(seed, kind, demo=True)
        start = time.monotonic()
        finding, trace, tokens = investigate(feeds, lambda payload: api("/api/chat", payload))
        cited = finding.get("evidence_ids") if isinstance(finding, dict) else None
        ok = (required_tool in [step["tool"] for step in trace if not step.get("rejected")] and isinstance(cited, list)
              and all(isinstance(i, str) for i in cited) and sorted(cited) == truth["evidence_ids"]
              and finding.get("disposition") == truth["disposition"])
        results.append({"scenario": kind, "passed": ok, "finding": finding, "expected_ids": truth["evidence_ids"],
                        "elapsed_seconds": round(time.monotonic() - start, 3), "generated_tokens": tokens, "trace": trace})
    return {"passed": all(r["passed"] for r in results), "model_digest": PIN["model_digest"], "cases": results}
