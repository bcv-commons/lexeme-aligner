#!/bin/bash
# Publish 2 batch: re-align all 14 editions (11 published langs + 3 second editions for pooling) with
# #2 (morph) in eflomal + gloss, then gapfill (#1 + #3, model-free) as a 3rd layer, then export+publish
# the 11 language partitions. (bash 3.2 compatible — no associative arrays.)
set -e
cd "$(dirname "$0")/.." || exit 1

align_one() {  # iso  usj-dir  lang-name
  iso="$1"; dir="$2"; name="$3"
  echo ">>> [$(date +%H:%M:%S)] align $iso ($name)"
  python3 -m lexeme_aligner.run_pilot --method eflomal --all --usj-dir "$dir" \
    --iso "$iso" --lang-name "$name" --eflomal-stem 2>&1 | grep -aiE "coverage|error" | tail -1
  python3 -m lexeme_aligner.export_lex --iso "$iso" --method eflomal --lang-name "$name" 2>&1 \
    | grep -aiE "rows|error" | tail -1
  python3 -m lexeme_aligner.run_pilot --method gloss --all --usj-dir "$dir" \
    --iso "$iso" --lang-name "$name" 2>&1 | grep -aiE "gloss] overall|error" | tail -1
  python3 -m lexeme_aligner.gapfill --iso "$iso" --all --usj-dir "$dir" 2>&1 \
    | grep -aiE "gapfill\] [0-9]+ gap|error" | tail -1
}

echo "########## STAGE 1: align all 14 editions ##########"
align_one arb  data/usj-arb        Arabic
align_one arbn data/usj-arbn       "Arabic (NAV)"
align_one asm  data/usj-asm        Assamese
align_one ben  data/usj-ben        Bengali
align_one eng  data/usj-eng        English
align_one engy data/usj-engy       "English (YLT)"
align_one fra  data/usj-fra-lsg    French
align_one hau  data/usj-hau-ohcb   Hausa
align_one hin  data/usj-hin        Hindi
align_one ind  data/usj-ind        Indonesian
align_one rus  data/usj-rus        Russian
align_one spa  data/usj-spa        Spanish
align_one swe  data/usj-swe        Swedish
align_one swk  data/usj-swk        "Swedish (Karnbibeln)"
echo "########## STAGE 1 DONE ##########"

publish_one() {  # iso  pool  lang-name
  iso="$1"; pool="$2"; name="$3"
  echo ">>> [$(date +%H:%M:%S)] export+publish $iso (pool=$pool)"
  if [ -n "$pool" ]; then
    python3 -m lexeme_aligner.export_lex --iso "$iso" --pool "$pool" --methods eflomal,gloss,gapfill \
      --lang-name "$name" --publish bcv-commons/lexeme-alignments --create 2>&1 \
      | grep -aiE "export_lex\]|publish\]"
  else
    python3 -m lexeme_aligner.export_lex --iso "$iso" --methods eflomal,gloss,gapfill \
      --lang-name "$name" --publish bcv-commons/lexeme-alignments --create 2>&1 \
      | grep -aiE "export_lex\]|publish\]"
  fi
}

echo "########## STAGE 2: export + publish 11 languages ##########"
publish_one arb arbn Arabic
publish_one asm ""   Assamese
publish_one ben ""   Bengali
publish_one eng engy English
publish_one fra ""   French
publish_one hau ""   Hausa
publish_one hin ""   Hindi
publish_one ind ""   Indonesian
publish_one rus ""   Russian
publish_one spa ""   Spanish
publish_one swe swk  Swedish
echo "########## STAGE 2 DONE — PUBLISH 2 COMPLETE ##########"
