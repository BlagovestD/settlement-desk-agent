# Benchmark provenance

The JSON receipts in [`results/benchmarks/`](../results/benchmarks/) are **immutable records of earlier runs**. The repository was subsequently reorganized (for example, `harness/` was split into `evaluation/`, `scripts/`, `settlement/`, and `config/`). The source-path and implementation SHA-256 fields in archived receipts therefore describe the **historical file tree**, not a fresh hash of the relocated source.

The offline tests continue to verify the frozen dataset, labels, reported scores, consistency of recorded run keys, and benchmark outcomes. Do **not** interpret a passing offline test as a new model inference or a re-attestation of the old source hash. To validate the current source against a local model, rerun the relevant benchmark commands shown in the README; new receipts will use the current implementation digests.
