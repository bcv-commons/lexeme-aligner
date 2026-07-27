#!/bin/bash
# Build + benchmark MERGED for all 12 main languages (the format-decision evidence).
# Per lang: merge (eflomal + gloss + gapfill, resolved by data/contest_rule.json) -> benchmark
# eflomal vs gloss vs merged (token-weighted top-1). Does NOT export/overwrite partitions — the
# merged jsonl + numbers are for the decision; the actual export happens at publish time by method.
cd "$(dirname "$0")/.." || exit 1
exec > out/_merged_all.log 2>&1
echo "=== MERGED-ALL BUILD+BENCH START $(date) ==="
[ -f data/contest_rule.json ] || echo "!! WARNING data/contest_rule.json missing — merge falls back to vote"

bench() {  # iso method [base_text]
  iso=$1; m=$2; bt=$3
  extra=""; [ -n "$bt" ] && extra="--base-text $bt"
  tw=$(python3 -m lexeme_aligner.benchmark --iso "$iso" --method "$m" --gold clear $extra 2>&1 \
       | grep -i "headline" | grep -oE "[0-9]+\.[0-9]+%" | head -1)
  echo "    $m: ${tw:-ERR}"
}

mergeBench() {  # iso name [base_text]
  iso=$1; name=$2; bt=$3
  echo ">>> [$iso $(date +%H:%M:%S)] merge (eflomal,gloss,gapfill + contest-rule)"
  python3 -m lexeme_aligner.merge_align --iso "$iso" --methods eflomal,gloss,gapfill \
    --contest-rule data/contest_rule.json || echo "!! merge $iso FAILED"
  echo "  BENCH $iso token-weighted top-1 (gold${bt:+ base_text=$bt}):"
  bench "$iso" eflomal "$bt"
  bench "$iso" gloss   "$bt"
  bench "$iso" merged  "$bt"
  echo "<<< [$iso] done"
}

mergeBench arb Arabic            AVD
mergeBench eng English           BSB
mergeBench fra French
mergeBench hau Hausa
mergeBench ind Indonesian
mergeBench swe Swedish
mergeBench swk "Swedish Karnbibeln"
mergeBench asm Assamese
mergeBench ben Bengali
mergeBench hin Hindi
mergeBench rus Russian
mergeBench spa Spanish
echo "=== MERGED-ALL COMPLETE $(date) ==="
