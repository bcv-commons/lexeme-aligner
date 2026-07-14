#!/bin/bash
# Complement NT for eng (BSB) + arb (arb_vdv) with the SAME editions as their OT.
# Per lang: ingest NT + align NT eflomal (pipeline) -> export whole-Bible -> gloss NT -> gapfill NT ->
# benchmark vs Clear gold (now includes Greek NT). Preserves the fresh v2 OT (ingest is NT-scoped).
cd "$(dirname "$0")/.." || exit 1
exec > out/_nt_complement.log 2>&1
echo "=== NT COMPLEMENT START $(date) ==="

complementNT() {  # iso edition name
  iso=$1; ed=$2; name=$3; dir=data/usj-$iso
  echo ">>> [$iso NT $(date +%H:%M:%S)] ingest+eflomal (pipeline --nt, edition=$ed)"
  python3 -m lexeme_aligner.pipeline --source helloao --translation "$ed" --iso "$iso" --lang-name "$name" --nt \
    || echo "!! pipeline $iso FAILED"
  echo ">>> [$iso NT] gloss"
  python3 -m lexeme_aligner.run_pilot --method gloss --nt --usj-dir "$dir" --iso "$iso" || echo "!! gloss $iso FAILED"
  echo ">>> [$iso NT] gapfill (publish-safe prior-gate)"
  HF_HUB_OFFLINE=1 python3 -m lexeme_aligner.gapfill --iso "$iso" --nt --usj-dir "$dir" \
    || echo "!! gapfill $iso FAILED"
  echo ">>> [$iso] BENCHMARK (OT+NT vs Clear gold):"
  python3 -m lexeme_aligner.benchmark --iso "$iso" --method eflomal --gold clear 2>&1 \
    | grep -iE "top-1|per word|token-weighted|coverage" | head -5
  echo "<<< [$iso] NT complement done"
}

complementNT eng BSB     English
complementNT arb arb_vdv Arabic
echo "=== NT COMPLEMENT COMPLETE $(date) ==="
