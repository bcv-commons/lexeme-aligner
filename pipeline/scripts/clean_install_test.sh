#!/bin/bash
# Clean-install test (2026-10-29): what someone else does with a copy of this repo, in an empty folder.
#   fresh copy -> new venv -> pip install -> lexeme-aligner fetch-inputs -> align-text on a public-domain sample (WEB: RUT JON MRK PHM)
# and checks: the run record says ok, the write guard refused nothing, compact files exist, the copy's git status is clean.
#
#   pipeline/scripts/clean_install_test.sh                  clone the committed repo (origin, HEAD)
#   pipeline/scripts/clean_install_test.sh --worktree       copy this checkout's tracked + new (not ignored) files instead: test before committing
#   PY=python3.12 KEEP=1 pipeline/scripts/clean_install_test.sh ...   another interpreter; keep the test folder afterwards
#   WORK=/some/dir ...                                       where to make the test folder (default: the system temp folder)
#   REPO_URL=https://github.com/bcv-commons/lexeme-aligner.git ...   clone this instead of origin (an outsider's anonymous clone)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
PY=${PY:-python3}
MODE=${1:-clone}
W=$(mktemp -d "${WORK:-${TMPDIR:-/tmp}}/lexeme-aligner-install.XXXXXX")
[ "${KEEP:-0}" = 1 ] || trap 'rm -rf "$W"' EXIT
echo "[clean-install] folder $W, python $($PY --version 2>&1)"

if [ "$MODE" = --worktree ]; then
  mkdir "$W/src"
  (cd "$HERE" && git ls-files -co --exclude-standard -z | "$PY" -c '
import os, shutil, sys
dst = sys.argv[1]
for f in sys.stdin.buffer.read().decode().split("\0"):
    if f and os.path.isfile(f):                          # a tracked file deleted in the checkout is left out
        os.makedirs(os.path.join(dst, os.path.dirname(f)), exist_ok=True)
        shutil.copy2(f, os.path.join(dst, f))' "$W/src")
  git -C "$W/src" init -q && git -C "$W/src" add -A && git -C "$W/src" -c user.name=t -c user.email=t@t commit -qm snapshot
else
  git clone -q "${REPO_URL:-$(git -C "$HERE" remote get-url origin)}" "$W/src"
fi

t0=$(date +%s)
"$PY" -m venv "$W/venv"
"$W/venv/bin/pip" install -q --upgrade pip
"$W/venv/bin/pip" install -q -e "$W/src[publish]"
echo "[clean-install] installed in $(( $(date +%s) - t0 )) s"

cd "$W"                                                  # not inside the copy: the commands must not depend on the working folder
"$W/venv/bin/lexeme-aligner" fetch-inputs --iso eng --data "$W/data"
"$W/venv/bin/lexeme-aligner" align-text --usj "$W/src/tests/data/sample-usj-engweb" --edition engweb_sample --iso eng \
    --lang-name English --out "$W/out" --data "$W/data"

"$W/venv/bin/python" - "$W" <<'EOF'
import json, sys
from pathlib import Path
w = Path(sys.argv[1])
rec = json.loads((w / "out/align-text-run.json").read_text())
compact = list((w / "out/compact").rglob("*.json"))
problems = [m for m, bad in [("run not ok", not rec.get("ok")), ("guard refusals", rec.get("guard_refusals")),
                             ("no compact files", not compact)] if bad]
print("[clean-install] steps:", ", ".join(f"{s['step']} {s['seconds']}s" for s in rec["steps"]))
print("[clean-install] compact files:", len(compact), "| missing settings:", [k for k, v in rec["settings"].items() if v.get("missing")])
if problems:
    sys.exit("[clean-install] FAILED: " + "; ".join(problems))
EOF
dirty=$(git -C "$W/src" status --porcelain)
[ -z "$dirty" ] || { echo "[clean-install] FAILED: the run changed the copy:"; echo "$dirty"; exit 1; }
echo "[clean-install] PASS in $(( $(date +%s) - t0 )) s"
