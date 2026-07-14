#!/usr/bin/env bash
# Gap-fill across ALL gold languages, one go. Per language:
#   ensure gloss  →  gapfill (all priors, MPS)  →  merge  →  aggregate benchmark + DIRECT gap-fill score.
# The DIRECT score is the honest gap metric: of the tokens eflomal+gloss missed, how many did gapfill
# fill CORRECTLY (by prior), which the aggregate top-1 hides. Clear langs = positional gold; swk/swe = karnbibeln.
#
#   bash scripts/gap_gold_batch.sh
cd "$(dirname "$0")/.." || exit 1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTORCH_ENABLE_MPS_FALLBACK=1

run() {
  local iso=$1 usj=$2 scope=$3 gold=$4
  echo ""; echo "############################## $iso ($scope, gold=$gold) ##############################"
  if ! ls "out/align_gloss_${iso}_"*.jsonl >/dev/null 2>&1; then
    echo "-- gloss $iso (bootstrap) --"
    python3 -m lexeme_aligner.run_pilot --method gloss $scope --usj-dir "$usj" --iso "$iso" \
      --lang-name "$iso" 2>&1 | grep -aiE "bootstrap|gloss] overall|Error|Traceback"
  fi
  echo "-- gapfill $iso (strong + positional + POS + translit) --"
  python3 -m lexeme_aligner.gapfill --iso "$iso" $scope --usj-dir "$usj" \
    2>&1 | grep -aiE "gapfill|Error|Traceback"
  echo "-- merge (eflomal+gloss+gapfill) --"
  python3 -m lexeme_aligner.merge_align --iso "$iso" --methods eflomal,gloss,gapfill 2>&1 | grep -aE "merged"
  if [ "$gold" = clear ]; then
    echo "-- aggregate top-1 (gloss vs merged) --"
    for m in gloss merged; do printf "  %-8s " "$m"
      python3 -m lexeme_aligner.benchmark --gold clear --iso "$iso" --method "$m" 2>&1 | grep -a headline
    done
  fi
  echo "-- DIRECT gap-fill score (the honest metric) --"
  python3 -m lexeme_aligner.score_gapfill --iso "$iso" --gold "$gold" 2>&1 | tail -9
}

run fra data/usj-fra-lsg   --all clear
run arb data/usj-arb       --ot  clear
run eng data/usj-eng       --ot  clear
run hau data/usj-hau-ohcb  --all clear
run swk data/usj-swk       --all lexicon
run swe data/usj-swe       --all lexicon
echo ""; echo "############################## BATCH DONE ##############################"
