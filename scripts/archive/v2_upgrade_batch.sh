#!/bin/bash
# v2 upgrade batch (NO publishing — user publishes afterwards).
#   Part 1  UPGRADE : re-align (eflomal → export lexeme-alignments → gloss) the 7 langs that lack t_idx
#                     (arb eng fra hau ind swe swk). The 5 already-current langs (asm ben hin rus spa)
#                     are kept as-is (fresh v2 builds with t_idx) — only gapfill-fed below.
#   Part 2  GAPFILL  : gapfill on ALL 12 (publish-safe prior-gate: strong+name, embedding dropped),
#                     bge-m3 on MPS. This is the long pole — left running.
#   Skips aligned_mwe (deferred); but the re-align gives t_idx so MWE is enabled for later.
cd "$(dirname "$0")/.." || exit 1
exec > out/_v2batch.log 2>&1
echo "=== V2 BATCH START $(date) ==="

reAlign() {  # iso dir scope name
  iso=$1; dir=$2; scope=$3; name=$4
  echo ">>> [upgrade $(date +%H:%M:%S)] $iso ($scope) dir=$dir"
  rm -f out/align_*_${iso}_*.jsonl                         # clean slate (old methods + ind stale numbering)
  python3 -m lexeme_aligner.run_pilot --method eflomal $scope --usj-dir "$dir" --iso "$iso" || echo "!! eflomal $iso FAILED"
  python3 -m lexeme_aligner.export_lex --iso "$iso" --method eflomal --lang-name "$name"     || echo "!! export  $iso FAILED"
  python3 -m lexeme_aligner.run_pilot --method gloss   $scope --usj-dir "$dir" --iso "$iso"  || echo "!! gloss   $iso FAILED"
  echo "<<< [upgrade] $iso done"
}

reAlign arb data/usj-arb       --ot  Arabic
reAlign eng data/usj-eng       --ot  English
reAlign fra data/usj-fra-lsg   --all French
reAlign hau data/usj-hau-ohcb  --all Hausa
reAlign ind data/usj-ind       --all Indonesian
reAlign swe data/usj-swe       --all Swedish
reAlign swk data/usj-swk       --all "Swedish Karnbibeln"

echo "=== PART 1 (upgrade) COMPLETE $(date) — starting gapfill ==="

gapfill_stage() {  # iso dir scope
  iso=$1; dir=$2; scope=$3
  echo ">>> [gapfill $(date +%H:%M:%S)] $iso ($scope)"
  HF_HUB_OFFLINE=1 python3 -m lexeme_aligner.gapfill --iso "$iso" $scope --usj-dir "$dir" \
    || echo "!! gapfill $iso FAILED"
  echo "<<< [gapfill] $iso done"
}

gapfill_stage arb data/usj-arb       --ot
gapfill_stage eng data/usj-eng       --ot
gapfill_stage fra data/usj-fra-lsg   --all
gapfill_stage hau data/usj-hau-ohcb  --all
gapfill_stage ind data/usj-ind       --all
gapfill_stage swe data/usj-swe       --all
gapfill_stage swk data/usj-swk       --all
gapfill_stage asm data/usj-asm       --all
gapfill_stage ben data/usj-ben       --all
gapfill_stage hin data/usj-hin       --all
gapfill_stage rus data/usj-rus       --all
gapfill_stage spa data/usj-spa       --all

echo "=== V2 BATCH COMPLETE $(date) ==="
