cd "$(dirname "$0")/.."
onb() {  # iso translation scope langname
  echo "############## $1 ($2, $3) ##############"
  echo "-- ingest + eflomal + export --"
  python3 -m lexeme_aligner.pipeline --source helloao --translation "$2" --iso "$1" $3 \
    --lang-name "$4" 2>&1 | grep -aiE "ingest|coverage|rows|Error|Traceback|ConnectionReset" | tail -6
  echo "-- gloss --"
  python3 -m lexeme_aligner.run_pilot --method gloss $3 --usj-dir "pipeline/work/ingest-cache/usj-$1" --iso "$1" \
    --lang-name "$4" 2>&1 | grep -aiE "bootstrap|gloss] overall|Error|Traceback" | tail -3
}
onb spa spa_r09 --all Spanish
onb rus rus_syn --all Russian
onb hin HINIRV  --all Hindi
onb asm asm_irv --nt Assamese
onb ben ben_irv --nt Bengali
echo "############## ONBOARD DONE ##############"
