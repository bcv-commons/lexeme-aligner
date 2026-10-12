"""USFM -> USJ with usfmtc, keeping whitespace-only text between elements.

usfmtc 0.4.8's USJ writer (`usjproc.convert_usx`) drops a child's tail when it is only whitespace
(`if child.tail and child.tail.strip() != ""`). USX keeps it, so `\\w créa\\w* \\w les\\w*` becomes
`créa` `les` with no space between them in USJ. Under the verse-text rule agreed with bcv-commons/bibles
(pieces inside a paragraph joined with no separator) that merges words: "créales". Found 2026-10-12 in
fra-lsg and ~80 PKF editions.

`convert(path)` returns the USJ dict with every tail kept. The patched writer is built once from usfmtc's
own source with that one condition removed; if usfmtc changes and the condition is gone, it fails loudly
instead of silently using the old behaviour.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

_BUGGY = 'if child.tail and child.tail.strip() != "":'
_FIXED = "if child.tail:"
_convert_usx = None


def _patched():
    global _convert_usx
    if _convert_usx is None:
        from usfmtc import usjproc
        src = inspect.getsource(usjproc.convert_usx)
        if _BUGGY not in src:
            raise RuntimeError("usfmtc.usjproc.convert_usx changed: check whether whitespace-only tails are still dropped")
        ns = dict(vars(usjproc))
        exec(compile(src.replace(_BUGGY, _FIXED), usjproc.__file__, "exec"), ns)
        ns["convert_usx"].__globals__["convert_usx"] = ns["convert_usx"]   # recursion uses the patched function
        _convert_usx = ns["convert_usx"]
    return _convert_usx


def convert(usfm_path) -> dict:
    import usfmtc
    from usfmtc import usjproc
    doc = usfmtc.readFile(str(usfm_path))
    out, _ = _patched()(doc.getroot())
    out["type"] = usjproc.SPEC_NAME
    out.setdefault("version", usjproc.VERSION_NUM)
    return out


def write(usfm_path, usj_path) -> None:
    Path(usj_path).write_text(json.dumps(convert(usfm_path), indent=2, ensure_ascii=False), encoding="utf-8")
