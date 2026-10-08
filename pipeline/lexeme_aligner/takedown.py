"""Per-edition takedown record for the compact-alignments datasets: `config/takedown.json`.

A rights holder, a licence change or our own policy can require that something derived from ONE edition is no longer published.
This file is the auditable record of such decisions (committed, like `config/senses_exclude.json`) and the single place the
generators read them from. Each entry is keyed by the edition's tag (the manifest entry's `tag`; compared case-insensitively with
every non-alphanumeric character read as `_`) and names how far the withdrawal goes:

    "edition"             the edition leaves every compact-alignments repo (main, -meta, -extra) and its manifest entry; the pool
                          builder (onboard.editions_for) no longer offers it, so a rebuild cannot bring it back.
    "rend"                the edition stays, its `rend` channel is not written at all.
    "rend_above_eflomal"  the edition stays; `rend` is written only for entries whose method is eflomal (`e`/`E`) and is `0`
                          ("withheld") for every entry another stage produced (gloss, gap-fill, span extension, ...). OPTIONAL: no
                          edition gets this unless it is listed here.

    {"editions": {"engwmv": {"withdraw": "rend", "reason": "...", "date": "2026-10-08"}}}

Pooled datasets (lexeme-alignments, aligned_mwe, senses-attested) drop an edition's rows by `base_text` through the existing
`config/senses_exclude.json` route; this file does not replace that.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path("config/takedown.json")
LEVELS = ("edition", "rend", "rend_above_eflomal")


def slug(tag: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in tag.lower())


def load(path: Path = PATH) -> dict[str, dict]:
    """{slug(tag): entry}; a missing file is an empty record. An unknown level is an error, never silently ignored."""
    if not Path(path).exists():
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for tag, e in (doc.get("editions") or {}).items():
        if tag.startswith("_"):
            continue
        if e.get("withdraw") not in LEVELS:
            raise ValueError(f"{path}: edition {tag!r} has withdraw={e.get('withdraw')!r}; expected one of {LEVELS}")
        out[slug(tag)] = dict(e, tag=tag)
    return out


def entry(tag: str, path: Path = PATH) -> dict | None:
    return load(path).get(slug(tag))


def is_withdrawn(tag: str, path: Path = PATH) -> bool:
    e = entry(tag, path)
    return bool(e and e["withdraw"] == "edition")


def withdrawn_slugs(path: Path = PATH) -> set[str]:
    return {k for k, e in load(path).items() if e["withdraw"] == "edition"}


def rend_scope(tag: str, path: Path = PATH) -> str:
    """'all' (the default), 'eflomal' (only eflomal entries carry an id) or 'none' (no channel)."""
    e = entry(tag, path)
    return {"rend": "none", "rend_above_eflomal": "eflomal"}.get(e["withdraw"], "all") if e else "all"
