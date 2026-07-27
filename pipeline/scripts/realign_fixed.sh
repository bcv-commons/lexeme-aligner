cd "$(dirname "$0")/.."
re() {  # iso scope
  echo "###### $1 ($2) — re-align with fixed tokenizer ######"
  python3 -m lexeme_aligner.run_pilot --method eflomal $2 --usj-dir "pipeline/work/ingest-cache/usj-$1" --iso "$1" --lang-name "$1" 2>&1 | grep -aiE "coverage|Error" | tail -2
  python3 -m lexeme_aligner.export_lex --iso "$1" --method eflomal 2>&1 | grep -aiE "rows|Error" | tail -1
  python3 -m lexeme_aligner.run_pilot --method gloss $2 --usj-dir "pipeline/work/ingest-cache/usj-$1" --iso "$1" --lang-name "$1" 2>&1 | grep -aiE "gloss] overall|Error" | tail -1
}
re hin --all
re asm --nt
re ben --nt
re rus --all
echo "###### REALIGN DONE ######"
