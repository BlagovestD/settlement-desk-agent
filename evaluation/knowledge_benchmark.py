"""Fixed synthetic payments-policy questions. Labels stay in the scoring process."""

from collections import Counter
from hashlib import sha256
import json
import os
import random
import statistics
import tempfile
import time

from evaluation.policy_corpus import documents, write_corpus
from settlement.knowledge import TOP_K, Scope, open_knowledge


# Each slice chooses a different edition from the same policy history. Totals
# are independently specified below for Atlas/US v1, with known slice deltas.
SLICES = (
    ("atlas-pay", "us", "2025-03-10", "2025-06-15", "v1", 0),
    ("atlas-pay", "us", "2025-04-12", "2025-05-03", "v2", 53),
    ("atlas-pay", "us", "2025-04-12", "2025-05-25", "v3", 106),
    ("cedar-pay", "ca", "2025-07-15", "2025-09-10", "v3", 224),
    ("northstar-pay", "gb", "2025-10-25", "2025-10-25", "v4", 395),
    ("seabright-pay", "us", "2025-09-12", "2025-08-08", "v3", 367),
    ("velora-pay", "gb", "2025-01-20", "2025-01-22", "v1", 410),
    ("meridian-pay", "ca", "2025-09-12", "2025-08-12", "v4", 625),
)

DEVELOPMENT = (
    ("Who signs off a release after the refund velocity alert trips?", "payout-reserves#refund-spike"),
    ("A settled refund is missing from the store books. What records should we retain?", "refund-handling#missing-ledger"),
    ("How much is the filing fee for a card dispute?", "fee-schedule#card-filing"),
    ("What is the combined fee for a bank payout trace and an approved retry of a rejected payout?",
     "fee-schedule#bank-trace fee-schedule#bank-retry", 1225),
    ("What do we charge in total to file a card dispute and prepare its evidence packet?",
     "fee-schedule#card-filing fee-schedule#card-packet", 2150),
    ("What evidence is needed to defend a digital delivery dispute?", "card-disputes#digital"),
    ("What is the reviewer step when accepting a payout explanation?", "reconciliation#owner"),
    ("Do we use the bank booking date or the value date for the close queue?", "bank-posting#posting-date"),
    ("How long do I keep a closed card dispute packet?", "data-controls#retention"),
    ("Can ACH Risk release a payout freeze caused by bank returns?", "payout-reserves#returns"),
    ("What is the an ACH R07 real-time transfer penalty in cents?", ""),
    ("Please state the Saturday refund batch cutoff and the Sunday retry fee.", ""),
    ("Who sets the limit for crypto collateral margin?", ""),
    ("What is the total cost of a card dispute filing and debit card token migration?", ""),
)

HOLDOUT = (
    ("The statement shows two indistinguishable deposits on the same day. What do we ask the processor to provide before matching?", "bank-posting#trace"),
    ("Our automatic payout never landed. How long after its expected arrival before we open a bank investigation?", "bank-posting#late"),
    ("What's the last UTC submission time for an automatic payout to go out on the same day?", "bank-posting#payout-cutoff"),
    ("The bank sent the money back. Which details are needed to release the original payout for retry?", "bank-posting#reversal"),
    ("Can we establish a unique credit assignment with only the clipped end of the statement reference?", "bank-posting#descriptor"),
    ("The customer says the parcel never arrived after an online card purchase. What should the carrier's proof show?", "card-disputes#receipt"),
    ("How much lead time do we need when sending a representment evidence packet to the acquirer?", "card-disputes#deadline"),
    ("What should the evidence packet contain when someone disputes two charges for one card purchase?", "card-disputes#duplicate"),
    ("A buyer alleges their card credit never settled. What refund identifiers should the dispute evidence include?", "card-disputes#refund-evidence"),
    ("If a software subscriber disputes delivery, what proof belongs in representment instead of a shipping receipt?", "card-disputes#digital"),
    ("The processor paid out a credit the shop never booked. What evidence do we keep before asking for a ledger fix?", "refund-handling#missing-ledger"),
    ("The issuer bounced a refund to a closed account. Where do we send the rejection reference?", "refund-handling#closed-card"),
    ("What is the maximum aggregate amount we may refund in several partial credits against one capture?", "refund-handling#part-refund"),
    ("How do we release a card hold when the authorization was never captured?", "refund-handling#void"),
    ("What happens to a card credit initiated after the scheduled refund batch closes?", "refund-handling#refund-cutoff"),
    ("Which team can let disbursements resume after a spike in refunds triggered a payout hold?", "payout-reserves#refund-spike"),
    ("Who gets the case when a payout is paused because the seller's identity review is outstanding?", "payout-reserves#kyc"),
    ("Once a reserve matures and its release posts to the ledger, when will it go out to the merchant?", "payout-reserves#reserve"),
    ("The seller's available balance dropped below zero. Who gets the funds snapshot and what should happen to payout retries?", "payout-reserves#negative"),
    ("A merchant asked us to itemize the fees on a bill. What should support send them?", "invoice-controls#breakdown"),
    ("The billing engine used the wrong contracted discount. What should finance attach before changing the charge?", "invoice-controls#discount"),
    ("At which stage of constructing a taxable invoice do we round service charges?", "invoice-controls#rounding"),
    ("A debit failed with ACH reason R01. Which checks must pass before another submission?", "ach-returns#r01"),
    ("How should an operator handle an ACH item returned under R03?", "ach-returns#r03"),
    ("The ACH network returned a consumer debit as unauthorized under R10. What is the next action?", "ach-returns#r10"),
    ("The ACH return file hasn't arrived. What acknowledgement and sequence information should we request?", "ach-returns#late-ach"),
    ("When does the window close for a domestic same-day ACH debit?", "ach-returns#same-day"),
    ("A source delivered an amended reconciliation file. How do we preserve lineage when taking the correction?", "reconciliation#raw"),
    ("Will replaying an identical source feed create a new import, and which key is used for replay?", "reconciliation#replay"),
    ("What is the earliest snapshot allowed to pick up a feed that arrives after the merchant close?", "reconciliation#timezone"),
    ("Can the three signed settlement amounts differ by a cent and still pass the ledger tolerance?", "reconciliation#penny"),
    ("When a watermark ends before the merchant's close, what do we need before calling the payout balanced?", "reconciliation#coverage"),
    ("What portion of a customer's payment card number may be included in the evidence notes?", "data-controls#pan"),
    ("Where does a suspicious transaction referral go, and what ID should be included?", "data-controls#sar"),
    ("How many days can I use a temporary analyst download before I have to generate it again?", "data-controls#export"),
    ("What should we check before we send the very first funds to a merchant's new settlement account?", "bank-change#verify-bank"),
    ("A seller unexpectedly changed their payout details. Which person should we call and what should happen to outgoing funds?", "bank-change#callback"),
    ("What's required if the account holder on a settlement account does not match the merchant's legal name?", "bank-change#name"),
    ("What do we bill for an exported copy of historical payouts outside the regular cycle?", "fee-schedule#history"),
    ("What's the charge to fix a broken settlement currency instruction?", "fee-schedule#fx-repair"),
    ("How much does it cost to stop an unposted payout rather than tracing it?", "fee-schedule#bank-stop"),
    ("What is the combined fee to open a card dispute and prepare a representment packet?", "fee-schedule#card-filing fee-schedule#card-packet", 2150),
    ("Add the fee for obtaining an automatic payout trace to the fee for retrying a returned bank payout.", "fee-schedule#bank-trace fee-schedule#bank-retry", 1225),
    ("What is the combined price for handling a domestic ACH return and resubmitting an ACH debit?", "fee-schedule#ach-return fee-schedule#ach-retry", 820),
    ("What do a duplicate archived bank statement and an off-cycle payout history export cost together?", "fee-schedule#statement fee-schedule#history", 940),
    ("What will a wire recall and a stop-payment request against an unposted payout cost in total?", "fee-schedule#bank-recall fee-schedule#bank-stop", 4500),
    ("What's the combined charge to withdraw one card dispute and escalate another to pre-arbitration?", "fee-schedule#card-withdrawal fee-schedule#card-escalation", 9000),
    ("Add the fees for domestic ACH account correction and domestic ACH return handling.", "fee-schedule#ach-correction fee-schedule#ach-return", 1205),
    ("Which ID should we get for two equal bank credits, and what is the fee for obtaining it?", "bank-posting#trace fee-schedule#bank-trace"),
    ("What is the domestic same-day ACH debit cutoff and what is the fee to retry a domestic ACH debit?", "ach-returns#same-day fee-schedule#ach-retry"),
    ("What do I preserve for a missing ledger refund, and who clears the payout hold from excessive refund velocity?", "refund-handling#missing-ledger payout-reserves#refund-spike"),
    ("How late can an automatic payout be submitted for same-day posting, and when do refund batches close?", "bank-posting#payout-cutoff refund-handling#refund-cutoff"),
    ("What is our fee to trace a missing domestic ACH return-file acknowledgement?", ""),
    ("When does Saturday's automatic payout submission window close?", ""),
    ("How many days does a cross-border ACH R01 return have to settle before a retry?", ""),
    ("How much do we charge to reverse a card-present wallet tokenization fee?", ""),
    ("What are the maximum number of card refund retries and the weekend cutoff for those retries?", ""),
    ("Who approves a freeze caused by abnormal cash withdrawal volume at ATMs?", ""),
    ("How long should we retain a closed chargeback packet for litigation hold after an appeal?", ""),
    ("What is the domestic same-day ACH cutoff, and what is the separate international ACH cutoff?", ""),
    ("What is the combined price of opening a card dispute and shipping a paper dispute packet internationally?", ""),
    ("Our acquirer rejected a representment packet. What is the limit on resubmissions?", ""),
    ("List the fee for account takeover insurance and the fee to add a new beneficiary.", ""),
    ("Which regulator issues the license covering our GB merchant settlement activities?", ""),
    ("Give the co-badged prepaid chargeback filing fee for Visa Electron only.", ""),
    ("What is the hardship waiver on an overdue duplicate bank statement fee?", ""),
)


def scenarios(development):
    rng = random.Random(340127 if development else 875641)
    rows = DEVELOPMENT if development else HOLDOUT
    plan = [(row, rng.randrange(len(SLICES)), "question") for row in rows]
    if not development:
        for index in (1, 2, 15, 41, 42, 43):
            plan.extend((rows[index], time_slice, "revision") for time_slice in (0, 1, 2, 5, 7)
                        if time_slice != plan[index][1])
        plan.extend((("What is the cost of obtaining a bank payout trace?", ""), i, "before-publication") for i in (0, 1, 2, 3))
    for index, (row, time_slice, category) in enumerate(plan, 1):
        question, labels, *total = row
        workspace, market, effective, known, edition, fee_offset = SLICES[time_slice]
        if category == "before-publication":
            known = "2024-12-10"
        expected = [f"{workspace}/{market}/{doc}/{edition}#{anchor}"
                    for doc, anchor in (s.split("#") for s in labels.split())]
        yield {"id": f"{index:03d}", "category": category, "question": question,
               "scope": dict(workspace=workspace, market=market, effective_on=effective, known_on=known),
               "expected_edition": None if category == "before-publication" else edition,
               "citations": sorted(expected), "total_cents": total[0] + fee_offset * len(expected) if total else None}


def sha(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def implementation_digest(root):
    paths = ("settlement/knowledge.py", "evaluation/knowledge_benchmark.py", "evaluation/policy_corpus.py", "settlement/model.py",
             "config/model-lock.json", "config/knowledge-model-lock.json")
    return sha({path: sha256((root / path).read_bytes()).hexdigest() for path in paths})


def quantiles(values):
    return {"median": round(statistics.median(values), 2), "p95": round(sorted(values)[(95 * len(values) + 99) // 100 - 1], 2)}


def run(root, development=False):
    rows = list(scenarios(development))
    folder = root / "artifacts" / ("knowledge-development" if development else "knowledge-holdout")
    path = folder / "policy-library.jsonl"
    write_corpus(path, documents())
    data_sha = sha(rows)
    if not development:
        manifest = json.loads((root / "results" / "benchmarks" / "knowledge-eval-manifest.json").read_text(encoding="utf-8"))
        if manifest != {"questions_sha256": data_sha, "corpus_sha256": sha256(path.read_bytes()).hexdigest()}:
            raise ValueError("The benchmark inputs differ from the frozen manifest")
    knowledge = open_knowledge(root, live=True, corpus_path=path)
    methods = ("lexical", "dense", "hybrid")
    generated = ("dense", "hybrid")
    sample = Scope(**rows[0]["scope"])
    knowledge.answer(sample, "What is the bank payout trace fee?")  # Exclude service warm-up.
    timing = {name: [] for name in generated}
    retrieval_times = []
    metrics = {name: Counter() for name in methods}
    result_rows = []
    progress = folder / "progress.jsonl"
    with progress.open("w", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            scope = Scope(**row["scope"])
            expected = set(row["citations"])
            started = time.perf_counter()
            ranked = knowledge.rankings(scope, row["question"])
            retrieval_ms = (time.perf_counter() - started) * 1000
            retrieval_times.append(retrieval_ms)
            # The oracle comes from the frozen scope slices, independently of the application filter.
            prefix = f"{scope.workspace}/{scope.market}/"
            edition = row["expected_edition"]
            visible = {key for key in knowledge.corpus.passages if edition is not None and key.startswith(prefix)
                       and key.split("#", 1)[0].endswith(f"/{edition}")}
            actual_scope = {hit.id for hit in knowledge.corpus.visible(scope)}
            if expected - visible or visible != actual_scope:
                raise ValueError(f"Scope or edition mismatch on benchmark case {row['id']}")
            case = {**row, "retrieval_ms": round(retrieval_ms, 2), "runs": {}}
            for method in methods:
                hits = [hit.id for hit in ranked[method]]
                observed = set(hits)
                metrics[method]["scope_violations"] += len(observed - visible)
                if expected:
                    metrics[method]["gold_passages_found"] += len(expected & observed)
                    metrics[method]["complete_evidence"] += int(expected <= observed)
                case["runs"][method] = {"retrieved": hits}
            order = generated if index % 2 else generated[::-1]
            for method in order:
                answer = knowledge.answer(scope, row["question"], method, ranked)
                citations = {hit.id for hit in answer["citations"]}
                answered = answer["status"] == "answered"
                correct = answered and citations == expected and answer["total_cents"] == row["total_cents"]
                correct = (correct if expected else not answered) and not answer["invalid_output"]
                metrics[method]["correct"] += int(correct)
                metrics[method]["correct_answerable"] += int(correct and bool(expected))
                metrics[method]["correct_abstentions"] += int(correct and not expected)
                metrics[method]["incorrect_answers"] += int(answered and not correct)
                metrics[method]["invalid_outputs"] += int(answer["invalid_output"])
                metrics[method]["exact_fee_totals"] += int(correct and row["total_cents"] is not None)
                metrics[method]["citation_scope_violations"] += len(citations - visible)
                timing[method].append(answer["elapsed_ms"])
                case["runs"][method].update({
                    "status": answer["status"], "correct": correct, "invalid_output": answer["invalid_output"],
                    "total_cents": answer["total_cents"],
                    "citations": sorted(citations), "generation_ms": answer["elapsed_ms"],
                })
            stream.write(json.dumps(case) + "\n")
            stream.flush()
            result_rows.append(case)
            if (index + 1) % 10 == 0:
                print(f"Knowledge benchmark: {index + 1}/{len(rows)} questions", flush=True)
    answerable = sum(bool(row["citations"]) for row in rows)
    gold_count = sum(len(row["citations"]) for row in rows)
    models = {item["name"]: item["size_vram"] for item in knowledge.models.api("/api/ps")["models"]}
    report = {
        "passed": all(metrics[method][key] == 0 for method in methods
                      for key in ("scope_violations", "citation_scope_violations", "invalid_outputs")),
        "development": development, "questions_sha256": data_sha, "corpus_sha256": knowledge.corpus.sha256,
        "knowledge_implementation_sha256": implementation_digest(root),
        "documents": sum(len(versions) for versions in knowledge.corpus.editions.values()),
        "passages": len(knowledge.corpus.passages), "distinct_embedding_inputs": len(knowledge.vectors),
        "scope_pairs": len(knowledge.corpus.scopes), "top_k": TOP_K,
        "questions": len(rows), "unique_questions": len({row["question"] for row in rows}),
        "answerable": answerable, "unanswerable": len(rows) - answerable,
        "gold_passages": gold_count, "fee_totals": sum(row["total_cents"] is not None for row in rows),
        "generator": knowledge.models.pin["model"], "generator_digest": knowledge.models.pin["model_digest"],
        "embedding_model": knowledge.models.pin["embedding_model"], "embedding_digest": knowledge.models.pin["embedding_digest"],
        "ollama_version": knowledge.models.version, "model_gpu_resident_bytes": models,
        "methods": {method: dict(metrics[method]) for method in methods},
        "all_rankings_ms": quantiles(retrieval_times), "generation_and_verification_ms": {k: quantiles(v) for k, v in timing.items()},
        "hybrid_end_to_end_ms": quantiles([row["retrieval_ms"] + row["runs"]["hybrid"]["generation_ms"] for row in result_rows]),
        "results": result_rows,
    }
    return report


def publish(root, payload):
    from evaluation.benchmark import write_json

    summary = {key: value for key, value in payload.items() if key != "results"}
    rows = payload["results"]
    summary["examples"] = {
        "fee_calculation": next((row for row in rows if row["total_cents"] is not None and row["runs"]["hybrid"]["correct"]), None),
        "hybrid_recovers_evidence": next((row for row in rows if row["runs"]["hybrid"]["correct"]
                                           and not row["runs"]["dense"]["correct"] and row["citations"]), None),
        "unanswerable": next((row for row in rows if not row["citations"] and row["runs"]["hybrid"]["correct"]), None),
        "hybrid_miss": next((row for row in rows if not row["runs"]["hybrid"]["correct"]), None),
    }
    write_json(root / "results" / "benchmarks" / "knowledge-benchmark.json", summary)


def freeze(root):
    folder = root / "artifacts" / "knowledge-holdout"
    path = folder / "policy-library.jsonl"
    write_corpus(path, documents())
    manifest = {"questions_sha256": sha(list(scenarios(False))), "corpus_sha256": sha256(path.read_bytes()).hexdigest()}
    output = root / "results" / "benchmarks" / "knowledge-eval-manifest.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    os.replace(stream.name, output)
