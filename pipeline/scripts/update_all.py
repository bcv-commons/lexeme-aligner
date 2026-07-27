"""Re-run the full 9-step chain for every currently-published language — `make update-all`."""
from __future__ import annotations

import json
import sys

from lexeme_aligner.config import LEX_ROOT
from lexeme_aligner.onboard_batch import run_one


def main() -> int:
    isos = sorted(json.loads((LEX_ROOT / "manifest.json").read_text(encoding="utf-8"))["languages"])
    print(f"[update-all] re-running the full chain for all {len(isos)} currently-published languages",
          file=sys.stderr)

    results = []
    for iso in isos:
        ok, note = run_one({"iso": iso}, full=True)
        results.append((iso, ok, note))
        print(f"  {'✓' if ok else '✗'} {iso:8} {note}", file=sys.stderr)

    succeeded = [iso for iso, ok, _ in results if ok]
    failed = [iso for iso, ok, _ in results if not ok]
    print(f"\n[update-all] {len(succeeded)}/{len(isos)} succeeded"
          + (f" — FAILED: {failed}" if failed else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
