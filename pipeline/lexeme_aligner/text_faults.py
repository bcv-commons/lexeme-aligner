"""Source-text faults recorded by bcv-query (aligner tables, text_faults.tsv; 2026-11-23): tokens of an edition's text that are not
words of the text (e.g. Latin book-end labels in heb_mod such as "Romans"). A fault whose fix is `drop` must get no link: the text is
NOT changed (token positions must keep matching the position tables), the links to those positions are left out when compact /
part-alignments are built (compact_align.build_compact). Faults with another fix (`replace`: the token is a real word, misspelt) keep
their links. The table is $ALIGNER_TEXT_FAULTS, else the vendored copy below; no table = no filter."""
import csv
import functools
import os
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[2] / "pipeline/vendor/bcv-query-text-faults/2026-11-23/text_faults.tsv"


@functools.lru_cache(maxsize=None)
def drops(edition: str, table: str | None = None) -> dict[tuple[str, int, int], frozenset[int]]:
    """{(book, chapter, verse): positions} of the edition's `drop` faults (the edition's own verse numbering, tokenizer positions)."""
    path = Path(table or os.environ.get("ALIGNER_TEXT_FAULTS") or DEFAULT)
    if not path.exists():
        return {}
    out: dict = {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if r.get("edition") == edition and (r.get("fix") or "").strip() == "drop" and r.get("status", "") != "withdrawn":
                k = (r["book"], int(r["chapter"]), int(r["verse"]))
                out[k] = out.get(k, frozenset()) | {int(r["position"])}
    return out
