"""Synthetic processor conversations and independent adjudication labels."""

import random

from evaluation.datasets import export
from evaluation.fixtures import build_case


# Text uses a separate convention in each case. A/B remain opaque bank references in the exported data.
DEVELOPMENT = [
    ("retry_correction", "B", (1, 2), [
        "Yesterday's handoff logged {A} as the release number for this payout.",
        "Treasury reran the release under {B} after the first attempt timed out.",
        "Correction for the bank team. Keep the rerun and disregard the handoff number. Only the rerun settled."
    ]),
    ("ordinal_reply", "A", (0, 1), [
        "Bank ops sent two records in this order: {B}, then {A}. Both show the same deposit amount.",
        "The second entry is ours. The first belongs to the reserve sweep."
    ]),
    ("scope_cross_reference", "A", (0, 1), [
        "Friday had a reserve sweep under {B} and a merchant release under {A}.",
        "This ticket is for the merchant release. The sweep has its own ticket, even though the amounts match."
    ]),
    ("representation_change", "B", (0, 1), [
        "The spreadsheet still shows {A}, which our portal generated when the transfer was prepared. The bank returned {B} in its settlement response.",
        "For this payout use the number returned by the bank. The portal value is only a pre-advice reference."
    ]),
    ("reversed_handoff", "A", (0, 2), [
        "Two numbers are in circulation: {A} for the morning file, {B} for the afternoon file.",
        "Assumed the afternoon file was ours. Checking with settlements.",
        "Settlements corrected that assumption. Our payout was in the morning file. The afternoon file was a different merchant."
    ]),
    ("contrast_in_one_message", "B", (0,), [
        "Please retire {A} from this ticket. That was the cancelled leg. We can identify the deposit from the replacement leg, whose bank paperwork came back with {B}."
    ]),
    ("explicit_reference", "A", (0,), [
        "Confirmed bank trace: {A}. Treasury has closed its review of this payout."
    ]),
    ("explicit_reference_second_row", "B", (1,), [
        "Treasury will reply after the file lands.",
        "Final settlement reference is {B}. This identifies the payout in this ticket."
    ]),
    ("alternatives_pending", None, (), [
        "Either {A} or {B} could be the deposit. Both show the same amount.",
        "We are waiting on Treasury to tell us which one belongs to this payout."
    ]),
    ("both_belong_elsewhere", None, (), [
        "We traced {B} to the reserve sweep and {A} to a different merchant.",
        "Neither of those numbers identifies the payout on this ticket. Our bank is still checking."
    ]),
    ("correction_after_cutoff", None, (), [
        "Bank sent {A} and {B}. Can you identify which credit is ours?",
        "The deposit on this ticket settled under {B}. We have now confirmed it with Treasury."
    ]),
]

HOLDOUT = [
    ("correction_reinstates_original", "A", (0, 2), [
        "The overnight batch lists {A} for the storefront payout. Recovery batch {B} has the same amount.",
        "We may have to use the recovery batch if the overnight run failed.",
        "Reconciliation confirms the overnight run posted. Recovery was called off before it went to the bank."
    ]),
    ("artifact_vs_credit", "B", (0, 1), [
        "{A} is printed in the settlement ticket header. The receiving bank's incoming credit screen shows {B}.",
        "Use the incoming credit screen value. The header number is just our internal ticket key."
    ]),
    ("other_merchant_same_amount", "A", (0, 1), [
        "We looked up the equal deposits. North kiosk received {B}. The online store received {A}.",
        "The current discrepancy came from online checkout. The kiosk has a separate payout and case."
    ]),
    ("chronology_and_partial_retraction", "B", (0, 2), [
        "The holiday staging file used {A}. The next business day's run used {B}.",
        "Earlier I said we'd need the holiday file because of the amount.",
        "Please retract my earlier note. The Friday amount was carried over and only the next business day's run posted."
    ]),
    ("bank_choice_by_sequence", "A", (0, 1), [
        "In the bank's listing, the credits appear as {A} followed by {B}. The list has two rows.",
        "The latter was our sponsor bank test. The former was the merchant payout this case is about."
    ]),
    ("negated_matching_reference", "B", (0,), [
        "The alert mentioned {A} because the two credits have equal amounts, but Treasury tied that number to its prefunding move. We received this merchant disbursement under {B}."
    ]),
    ("canonical_confirmed", "A", (0,), [
        "Treasury verified the payout. Verified settlement reference: {A}."
    ]),
    ("canonical_final", "B", (0,), [
        "Confirmed trace is {B}. Bank operations checked the posting for this payout."
    ]),
    ("only_pending", None, (), [
        "Candidates from the bank are {A} and {B}. Neither has been linked to this payout yet.",
        "Could someone in Treasury check whether one of these credits is ours?"
    ]),
    ("only_exclusions", None, (), [
        "Reserve sweep is {A}; the test deposit is {B}. Both are separate from this merchant's payout.",
        "The receiving bank has not supplied a reference for the merchant payout."
    ]),
    ("confirmation_not_yet_visible", None, (), [
        "Two unconfirmed options {A} and {B}. Please investigate which one cleared for this payout.",
        "Bank operations confirmed that this merchant's payout cleared with {A}."
    ]),
]


def support_cases(development=False):
    scenarios = DEVELOPMENT if development else HOLDOUT
    for index, (name, answer, required, messages) in enumerate(scenarios):
        seed = (31840 if development else 59350) + index
        case, feeds, _ = build_case(seed, "ambiguous_bank")
        rng = random.Random(seed)
        refs = rng.sample(range(100000, 999999), 2)
        traces = {key: f"TR{ref}" for key, ref in zip(("A", "B"), refs)}
        bank = feeds["bank"]
        feeds["processor"]["trace"] = None
        for label, credit in zip(("A", "B"), bank["credits"]):
            credit["trace"] = traces[label]
        notes = [{
            "id": f"note_{rng.getrandbits(32):08x}",
            "posted_at": ("2026-01-16T00:02:00Z" if name in {"correction_after_cutoff", "confirmation_not_yet_visible"} and i == 1
                          else f"2026-01-15T20:{i:02d}:00Z"),
            "text": message.format(**traces)
        } for i, message in enumerate(messages)]
        feeds["support"] = {**{key: feeds["merchant"][key] for key in ("account", "case_id", "currency", "cutoff")},
                            "complete": True, "notes": notes}
        expected = {"candidate_id": next((row["id"] for row in bank["credits"] if row["trace"] == traces.get(answer)), None),
                    "trace": traces.get(answer), "required_notes": [notes[i]["id"] for i in required]}
        yield case, name, feeds, expected


def export_support_cases(folder):
    return {name: export(folder / name, feeds) for _, name, feeds, _ in support_cases()}
