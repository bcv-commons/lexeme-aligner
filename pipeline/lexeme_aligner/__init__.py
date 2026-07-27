"""aligner — the Strong's word-alignment factory (offline producer).

Design: docs/aligner-plan.md. Generic pipeline (any language via a source adapter +
iso639-3); Indonesian is the pilot. Stage (a) = the $0 deterministic gloss-anchored
strategy; stage (b) = eflomal (statistical); gapfill = model-free gap-filling for the
tokens neither aligned. Language-independent throughout — no target-language model,
runs on any language with a translation. Experiment artifacts go to aligner/out/
(gitignored); nothing writes to resources/ until it passes the benchmark gate.
"""
