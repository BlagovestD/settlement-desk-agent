# Payout snapshot inputs

A bundle is a UTF-8 JSON manifest and three CSVs in the same directory. A bundle publishes one payout snapshot atomically. Submit multiple manifest paths to process a day of payouts. Use `python run.py seed` to produce readable sample files.

```json
{
  "schema": 1,
  "account": "sample-merchant",
  "case_id": "case_a",
  "currency": "USD",
  "cutoff": "2026-01-16T00:00:00Z",
  "version": 1,
  "supersedes": null,
  "sources": {
    "merchant": {"file": "merchant.csv", "coverage": "2026-01-16T00:00:00Z"},
    "processor": {"file": "processor.csv", "coverage": "2026-01-16T00:00:00Z"},
    "bank": {"file": "bank.csv", "coverage": "2026-01-16T00:00:00Z"}
  }
}
```

Every ID uses ASCII letters, digits, underscores or hyphens. CSV headers follow the documented order. Leave unused fields empty. All money is signed integer cents. Use canonical UTC timestamps with second precision. Ledger and processor rows represent the payout as of the manifest cutoff. The supplied bank statement is scoped to the payout's candidate window. Rows posted after the cutoff remain visible in provenance and are excluded from the observed bank total.

| File | Header | Rows |
| --- | --- | --- |
| Merchant | `id,kind,charge_id,refund_id,gross,fee,cents` | `charge`, `refund`, `fee`, `receipt`. Receipt rows carry `charge_id`, `refund_id` and negative `cents`. |
| Processor | `id,kind,charge_id,gross,fee,trace,cents` | One positive `payout` header with `cents` and optional trace. Item rows use `charge`, `refund` and `fee`. |
| Bank | `id,trace,cents,posted_at` | Positive posted credits. Keep bank credit IDs consistent across candidate windows in the same account. |
| Support (optional) | `id,posted_at,text` | Processor messages scoped to this payout. Use up to 2,000 characters per message and quote commas or line breaks as standard CSV fields. |

Charges are positive, refunds negative, and fee-only lines carry zero gross and a positive fee. Matching merchant and processor entries use the same economic event ID. Refund entries carry the original charge ID. A negative ledger lookup requires a complete merchant watermark. Null means the bank credit remains unconfirmed, so the bank variance is unknown. The same credit cannot establish a match for competing payouts.

Reimporting the identical manifest and source contents returns the existing snapshot. Different contents for the same account, case and version go to quarantine. To correct a record, retain the case and payout IDs, increment `version`, and set `supersedes` to the preceding version. The cutoff may stay the same or advance. Source rows retain their original field values, file checksum and CSV line locator.

To supply support notes, add a `"support": {"file": "support.csv", "coverage": "2026-01-16T00:00:00Z"}` entry under `sources`. Only messages at or before the snapshot cutoff enter the investigation. The support workflow applies when the structured inputs are complete, the merchant and processor agree, and the payout lacks a structured trace. A proposed trace must identify one equal-amount bank credit free of structured claims from other payouts. Reconciliation still requires a structured processor trace in a later snapshot.
