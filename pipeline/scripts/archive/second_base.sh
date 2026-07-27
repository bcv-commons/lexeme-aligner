#!/bin/bash
# Build + benchmark the SECOND gold base_text per language (validation of the method on a 2nd edition):
#   engy = English Young's Literal (helloAO eng_ylt) → benchmark vs eng gold, base_text=YLT
#   arbn = Arabic New Arabic Version (helloAO ARBNAV) → benchmark vs arb gold, base_text=ONAV
# eflomal + gloss only (no gapfill — these are validation editions, not the merged-all set). Whole Bible.
cd "$(dirname "$0")/.." || exit 1
exec > out/_secondbase.log 2>&1
echo "=== SECOND-BASE BUILD START $(date) ==="

build2() {  # iso translation name gold_iso base_text
  iso=$1; ed=$2; name=$3; giso=$4; bt=$5; dir=data/usj-$iso
  echo ">>> [$iso ($ed) $(date +%H:%M:%S)] ingest+eflomal (pipeline --all)"
  python3 -m lexeme_aligner.pipeline --source helloao --translation "$ed" --iso "$iso" --lang-name "$name" --all \
    || echo "!! pipeline $iso FAILED"
  echo ">>> [$iso] gloss"
  python3 -m lexeme_aligner.run_pilot --method gloss --all --usj-dir "$dir" --iso "$iso" || echo "!! gloss $iso FAILED"
  echo ">>> [$iso] BENCHMARK vs '$giso' gold, base_text=$bt (eflomal, OT+NT):"
  python3 -m lexeme_aligner.benchmark --iso "$iso" --method eflomal --gold clear --gold-iso "$giso" --base-text "$bt" 2>&1 \
    | grep -iE "top-1|per word|token-weighted|coverage" | head -5
  echo "<<< [$iso] second-base done"
}

build2 engy eng_ylt "English (Young's Literal)"      eng YLT
build2 arbn ARBNAV  "Arabic (New Arabic Version)"     arb ONAV
echo "=== SECOND-BASE BUILD COMPLETE $(date) ==="
