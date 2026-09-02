"""Re-run the full 9-step chain for every currently-published language — `make update-all`.

Resumable by design. The sweep is ~1,626 languages at roughly 80s each — on the order of a day and
a half on a box that reboots unannounced and shares its volume with other jobs that can fill it.
Without a resume marker a crash at hour 30 throws the whole run away and starts again at `aaa`
(that is exactly what happened on 2026-09-02: the disk filled at 00:37, systemd restarted the
service 756 times over 6h20m, and the surviving run began the alphabet again — ~15h lost). So each
language that finishes is recorded in a state file and skipped on the next start; `--fresh` starts
a genuinely new sweep.

The free-space guard exists for the same incident: when `/` is full, every write in the chain dies
with ENOSPC, including the log redirect in run_update_all.sh, so the failures are instantaneous and
systemd spins. Checking before each language turns that into one clear message per attempt, and the
sweep picks up where it left off as soon as space comes back.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT
from lexeme_aligner.onboard_batch import run_one

DEFAULT_STATE = Path("pipeline/work/logs/update_all_state.json")
MIN_FREE_GB = 20.0


def _load_state(path: Path, fresh: bool) -> dict:
    if fresh or not path.exists():
        return {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "done": [], "failed": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "done": [], "failed": []}
    state.setdefault("done", [])
    state.setdefault("failed", [])
    return state


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1024 ** 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-ingest", action="store_true",
                    help="no network fetch at all — re-process already-cached text for every language "
                         "(fails/skips gracefully for any language whose usj cache isn't present)")
    ap.add_argument("--clean-out", action="store_true",
                    help="clean each language's out/ raw jsonl right after ITS OWN chain finishes — "
                         "keeps out/ from growing across the whole run instead of only at the end")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE,
                    help=f"resume marker; languages listed there are skipped (default {DEFAULT_STATE})")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any existing state file and re-process every language from scratch")
    ap.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB,
                    help="abort (non-zero, so a supervisor retries later) when free space on the "
                         f"output volume drops below this, instead of dying mid-write (default {MIN_FREE_GB})")
    args = ap.parse_args()

    isos = sorted(json.loads((LEX_ROOT / "manifest.json").read_text(encoding="utf-8"))["languages"])
    state = _load_state(args.state, args.fresh)
    done = set(state["done"])
    todo = [i for i in isos if i not in done]

    print(f"[update-all] re-running the full chain for all {len(isos)} currently-published languages"
          + (" (--skip-ingest: no network fetch)" if args.skip_ingest else "")
          + (" (--clean-out: per-language)" if args.clean_out else ""), file=sys.stderr)
    if done:
        print(f"[update-all] resuming sweep started {state.get('started', '?')}: "
              f"{len(done)} already done, {len(todo)} to go (state: {args.state})", file=sys.stderr)

    for iso in todo:
        free = _free_gb(LEX_ROOT)
        if free < args.min_free_gb:
            print(f"\n[update-all] ABORT before '{iso}': only {free:.1f} GB free on the output volume "
                  f"(need {args.min_free_gb:.0f}). {len(done)} languages done and recorded — free space "
                  f"and the sweep resumes from here.", file=sys.stderr)
            return 2

        lang = {"iso": iso}
        if args.skip_ingest:
            lang["skip_ingest"] = True
        if args.clean_out:
            lang["clean_out"] = True
        ok, note = run_one(lang, full=True)
        print(f"  {'✓' if ok else '✗'} {iso:8} {note}", file=sys.stderr)

        done.add(iso)
        state["done"] = sorted(done)
        if not ok and iso not in state["failed"]:
            state["failed"].append(iso)
        _save_state(args.state, state)

    failed = state["failed"]
    print(f"\n[update-all] {len(done) - len(failed)}/{len(isos)} succeeded"
          + (f" — FAILED: {failed}" if failed else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
