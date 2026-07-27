"""Re-run the full 9-step chain for every currently-published language — `make update-all`."""
from __future__ import annotations

import argparse
import json
import sys

from lexeme_aligner.config import LEX_ROOT
from lexeme_aligner.onboard_batch import run_one


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-ingest", action="store_true",
                    help="no network fetch at all — re-process already-cached text for every language "
                         "(fails/skips gracefully for any language whose usj cache isn't present)")
    ap.add_argument("--clean-out", action="store_true",
                    help="clean each language's out/ raw jsonl right after ITS OWN chain finishes — "
                         "keeps out/ from growing across the whole run instead of only at the end")
    args = ap.parse_args()

    isos = sorted(json.loads((LEX_ROOT / "manifest.json").read_text(encoding="utf-8"))["languages"])
    print(f"[update-all] re-running the full chain for all {len(isos)} currently-published languages"
          + (" (--skip-ingest: no network fetch)" if args.skip_ingest else "")
          + (" (--clean-out: per-language)" if args.clean_out else ""), file=sys.stderr)

    results = []
    for iso in isos:
        lang = {"iso": iso}
        if args.skip_ingest:
            lang["skip_ingest"] = True
        if args.clean_out:
            lang["clean_out"] = True
        ok, note = run_one(lang, full=True)
        results.append((iso, ok, note))
        print(f"  {'✓' if ok else '✗'} {iso:8} {note}", file=sys.stderr)

    succeeded = [iso for iso, ok, _ in results if ok]
    failed = [iso for iso, ok, _ in results if not ok]
    print(f"\n[update-all] {len(succeeded)}/{len(isos)} succeeded"
          + (f" — FAILED: {failed}" if failed else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
