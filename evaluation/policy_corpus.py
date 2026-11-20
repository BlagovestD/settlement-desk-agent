"""Synthetic policy documents. This module contains no benchmark questions or labels."""

import json


WORKSPACES = ("atlas-pay", "cedar-pay", "northstar-pay", "seabright-pay", "velora-pay", "meridian-pay")
MARKETS = ("us", "ca", "gb")
EDITIONS = (
    ("v1", "2025-01-01", "2024-12-15"),
    ("v2", "2025-04-01", "2025-03-17"),
    ("v3", "2025-04-01", "2025-05-17"),
    ("v4", "2025-09-01", "2025-08-10"),
)


def rules(edition):
    cutoff = ("14:30", "15:00", "15:15", "16:00")[edition]
    days = ("two", "three", "four", "five")[edition]
    team = ("Payments Risk", "Settlement Controls", "Treasury Controls", "Merchant Risk")[edition]
    return {
        "bank-posting": ("Bank statement and posting exceptions", [
            ("trace", "Unidentified credit", "Ambiguous credit match", "When two incoming bank credits have the same amount and posting day, request the bank trace from the processor before assigning either credit."),
            ("late", "Posting window", "No posted credit", f"If an automatic payout has no posted bank credit, wait {days} business days after the expected posting date before opening a bank investigation."),
            ("payout-cutoff", "Posting window", "Same-day cutoff", f"The submission cutoff for a same-day automatic payout is {cutoff} UTC. Items submitted after the cutoff are handled the next business day."),
            ("reversal", "Returned credit", "Rejected payout", "A rejected payout requires the bank's return reason and the original payout identifier before the amount can be released for retry."),
            ("posting-date", "Statement parsing", "Value date", "Use the statement booking date for the close queue. Keep the value date as a separate field for the treasury statement."),
            ("descriptor", "Unidentified credit", "Truncated statement descriptor", "A clipped reference suffix is corroborating evidence only. A full end-to-end payment identifier is required for a unique assignment."),
        ]),
        "card-disputes": ("Card dispute handling playbook", [
            ("receipt", "Merchandise not received", "Proof of delivery", "For a card-not-present merchandise-not-received chargeback, attach carrier delivery confirmation showing the delivery address and completion date."),
            ("deadline", "Evidence handoff", "Acquirer dispute deadline", "Submit the evidence packet to the acquirer at least two business days before the case-specific response deadline in the dispute portal."),
            ("duplicate", "Processing error", "Duplicate charge", "For a duplicate processing dispute, attach both authorization identifiers and the capture timestamps so the acquirer can distinguish the transactions."),
            ("refund-evidence", "Credit not processed", "Refund proof", "For a credit-not-processed dispute, supply the settled refund reference and receipt timestamp. A pending refund receipt is insufficient."),
            ("issuer-window", "Evidence handoff", "Issuer review window", "The issuer's decision window begins after the representment packet is accepted by the acquirer. Use the case-specific deadline in the dispute portal."),
            ("digital", "Merchandise not received", "Digital delivery", "For a digital service dispute, attach account access logs and activation details instead of a physical carrier receipt."),
        ]),
        "refund-handling": ("Refund and credit operations", [
            ("missing-ledger", "Ledger exception", "Missing merchant refund", "When the processor settled a refund missing from the merchant ledger, retain the original purchase identifier, refund event and settlement line before requesting a ledger correction."),
            ("closed-card", "Refund destination", "Closed card account", "A refund must first be attempted on the original payment instrument. If the issuer returns it, escalate with the rejection reference to Payments Operations."),
            ("part-refund", "Refund limits", "Multiple partial refunds", "The sum of settled partial refunds cannot exceed the original captured purchase amount. Pending attempts are reviewed separately."),
            ("void", "Authorization lifecycle", "Uncaptured authorization", "Void an uncaptured authorization to release the hold. A refund is available after capture."),
            ("refund-cutoff", "Processing window", "Refund batch cutoff", "The scheduled refund batch closes at 17:00 UTC. A refund initiated later enters the following business day's batch."),
        ]),
        "payout-reserves": ("Merchant reserve and payout holds", [
            ("refund-spike", "Risk holds", "Refund velocity release approval", f"For a payout paused because refund velocity breached its alert limit, the release requires sign-off from {team}."),
            ("kyc", "Risk holds", "Identity verification release", "Compliance Operations clears holds linked to outstanding merchant identity verification after the case has the approved review decision."),
            ("reserve", "Reserve movements", "Rolling reserve release", "A reserve maturity releases on the next scheduled automatic payout after the ledger's reserve release event posts."),
            ("negative", "Negative position", "Negative available balance", "If merchant available funds are negative, stop discretionary payout retries and open a Collections review with the current balance snapshot."),
            ("returns", "Risk holds", "Bank return rate freeze", "A high bank return rate freeze requires approval from Bank Risk before settlement can resume."),
        ]),
        "invoice-controls": ("Merchant billing and fee controls", [
            ("debit", "Invoice adjustments", "Duplicate invoice debit", "Two invoice debits with the same invoice and amount require comparison of their bank references before a duplicate reversal is raised."),
            ("breakdown", "Invoice support", "Invoice fee breakdown", "A merchant requesting a fee breakdown receives the invoice identifier, the affected service codes and the billing period."),
            ("discount", "Fee corrections", "Sales discount mismatch", "Attach the signed commercial amendment and invoice item before adjusting a fee that differs from the contracted discount."),
            ("rounding", "Fee corrections", "Line item rounding", "Round each taxable fee line to currency precision before summing the invoice. Retain the original unrounded taxable base for audit."),
        ]),
        "ach-returns": ("ACH returns operations guide", [
            ("r01", "Retry decision", "Insufficient funds ACH return", "An R01 retry requires current authorization and a fresh available-balance check before resubmission."),
            ("r03", "Retry decision", "No account ACH return", "An R03 return goes to bank account correction. Do not resubmit until the replacement account is verified."),
            ("r10", "Unauthorized debit", "Consumer disputes ACH debit", "Route an R10 consumer unauthorized debit to ACH Risk and suspend retries on the authorization until review is complete."),
            ("late-ach", "Posting window", "Missing ACH return file", "If a return file is late, ask Bank Integrations for the file sequence number and processing acknowledgement."),
            ("same-day", "Origination windows", "Domestic same-day ACH debit cutoff", "The domestic same-day ACH debit cutoff is 13:00 UTC. International transfers use a separate schedule."),
        ]),
        "reconciliation": ("Daily reconciliation controls", [
            ("raw", "Evidence preservation", "Corrected source file", "Preserve the original accepted input and store the corrected feed as a superseding snapshot with its own file digest and version."),
            ("replay", "Evidence preservation", "Exact import replay", "An identical input file for the same source is idempotent. The source digest is the replay key."),
            ("timezone", "Snapshot eligibility", "Merchant close timezone", "Normalize timestamps to UTC after applying the merchant's declared close timezone. A late arrival is included only in the next eligible snapshot."),
            ("penny", "Variance resolution", "Payout rounding variance", "A zero tolerance applies to the three signed settlement differences. Record the residual in integer cents and retain the original line items."),
            ("owner", "Variance resolution", "Resolved case approval", "A reviewer records the evidence references and reason code before accepting a proposed settlement explanation."),
            ("coverage", "Snapshot eligibility", "Incomplete source coverage", "If any source coverage watermark is before merchant close, request the missing source interval before considering a payout balanced."),
        ]),
        "data-controls": ("Operations data handling", [
            ("pan", "Restricted evidence", "Card number in case notes", "Use an approved token or masked last four digits in case notes; do not paste the full card number."),
            ("sar", "Restricted evidence", "Suspicious activity review", "Send a suspicious activity referral through the restricted investigations queue using the internal case identifier only."),
            ("retention", "Evidence retention", "Closed dispute file retention", "Retain the evidence packet for a closed card dispute for twelve months following final adjudication."),
            ("export", "Evidence retention", "Analyst export retention", "The temporary analyst export expires after seven calendar days and may be regenerated from the audit record."),
        ]),
        "bank-change": ("Merchant bank detail verification", [
            ("verify-bank", "Settlement beneficiary", "Replacement payout account", "Verify new settlement account ownership through the secure verification workflow and require independent reviewer approval before the first payout."),
            ("callback", "Settlement beneficiary", "Account takeover alert", "For an unexpected payout-account change, call the verified merchant contact from onboarding records and freeze outgoing payouts pending review."),
            ("name", "Account ownership", "Beneficiary name mismatch", "A mismatch between the settlement bank beneficiary and merchant legal name requires a documented ownership exception from Compliance Operations."),
        ]),
    }


FEES = (
    ("card-filing", "Card disputes", "Card dispute filing fee", 1500, "Charged to open a card dispute case."),
    ("card-packet", "Card disputes", "Card dispute evidence packet fee", 650, "Charged when preparing a card dispute representment packet."),
    ("card-withdrawal", "Card disputes", "Card dispute withdrawal fee", 300, "Charged for withdrawing a representment request."),
    ("card-escalation", "Card disputes", "Card dispute escalation fee", 8700, "Charged when a card dispute advances to pre-arbitration."),
    ("bank-trace", "Payout exceptions", "Bank payout trace fee", 950, "Charged for obtaining a bank trace on an automatic payout."),
    ("bank-retry", "Payout exceptions", "Rejected payout retry fee", 275, "Charged for resubmitting a returned bank payout after approval."),
    ("bank-recall", "Payout exceptions", "Payout wire recall fee", 2400, "Charged for submitting a payout wire recall request."),
    ("bank-stop", "Payout exceptions", "Payout stop payment fee", 2100, "Charged for a stop order against an unposted disbursement."),
    ("ach-return", "ACH operations", "Domestic ACH return handling fee", 480, "Charged for processing one domestic ACH return."),
    ("ach-retry", "ACH operations", "Domestic ACH debit retry fee", 340, "Charged for resubmitting a domestic ACH debit."),
    ("ach-correction", "ACH operations", "ACH account correction fee", 725, "Charged when a notification of change requires correction of ACH account details."),
    ("statement", "Treasury services", "Duplicate bank statement fee", 550, "Charged for a replacement copy of an archived bank statement."),
    ("history", "Treasury services", "Payout history export fee", 390, "Charged for a nonstandard off-cycle export of historical payouts."),
    ("fx-repair", "Treasury services", "Currency instruction repair fee", 1400, "Charged to repair an incorrect settlement currency instruction."),
)


def documents(workspaces=WORKSPACES, markets=MARKETS):
    for workspace in workspaces:
        for market in markets:
            offset = WORKSPACES.index(workspace) * 87 + MARKETS.index(market) * 31
            for index, (version, effective, published) in enumerate(EDITIONS):
                headers = dict(workspace=workspace, market=market, version=version, effective=effective, published=published)
                for key, (title, rows) in rules(index).items():
                    yield {**headers, "document": key, "title": title, "passages": [
                        dict(anchor=anchor, section=section, label=label, text=text) for anchor, section, label, text in rows
                    ]}
                yield {**headers, "document": "fee-schedule", "title": "USD merchant operations fee schedule", "passages": [
                    dict(anchor=anchor, section=section, label=label, text=text,
                         usd_cents=base + offset + 53 * index) for anchor, section, label, base, text in FEES
                ]}


def write_corpus(path, docs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for doc in docs:
            stream.write(json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n")
