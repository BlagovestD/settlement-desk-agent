# Policy library import

The knowledge desk reads UTF-8 JSON Lines. Each line is a complete edition of a policy document. An edition contains complete clauses or table rows with stable anchors, so indexing never cuts through an evidence passage.

```json
{"workspace":"atlas-pay","market":"us","document":"fee-schedule","title":"USD operations fees","version":"v2","effective":"2025-04-01","published":"2025-03-17","passages":[{"anchor":"bank-trace","section":"Payout exceptions","label":"Bank payout trace fee","text":"Charged to obtain a bank trace for an automatic payout.","usd_cents":1003}]}
```

- `workspace`, `market`, `document`, `version` and `anchor` use lowercase letters, digits and hyphens. An anchor must be unique within the edition. Document families are identified by `(workspace, market, document)`.
- `effective` and `published` use `YYYY-MM-DD`. The application first filters by workspace, market, effective date and knowledge date. It selects the latest eligible effective date for each document family, then the latest publication for that date. Two editions with identical date pairs in a family are invalid.
- `title`, `section`, `label` and `text` provide the search context. `text` holds the exact clause or the description for a typed fee row. Include `usd_cents` for a fixed fee in USD. Its value is a nonnegative integer, including zero when a fee is waived.
- `workspace` and `market` are request filters in this loopback demo. The application lets its local operator select either. Identity-based access control would need an authenticated service boundary.

```powershell
pwsh -File scripts/with-model.ps1 -Task demo-rag -KnowledgeCorpus .\my-library.jsonl
python run.py serve --knowledge-corpus .\my-library.jsonl
```

The first command starts live retrieval and answer generation. The second starts offline keyword search. Validation happens before the web server opens. Live indexing stores normalized embeddings by input and model digest under `.runtime/`, and rebuilding a library reuses unchanged inputs. A source link identifies one edition and anchor and records the SHA-256 of the original JSONL line.
