# Changelog

## 1.0.0 (2026-11-28)

First release after the handover from make-tanakh (agreed 2026-11-28).

- Engine taken verbatim from bcv-commons/make-tanakh v0.14.0, `scripts/morphs.py` lines 107-976 (detect_tone, Morph, clusters,
  ToneSpec, ToneMorph, Segmenter). The engine last changed there in v0.9.0. The file is byte-identical in make-tanakh's v0.14.1 commit
  f9bdf70 (not yet tagged when this was taken).
- Dropped the `grammar` / `register` imports (types only) and the template's store tooling.
- One file: the bundle loader (`load`, `Seg.split`) is included, with the same API as a bundle's own `bcv_segment.py`.
- Checked byte-identical (`split(w)` and `S.split(w, tones=True)`) against each published bundle's own engine copy, on every distinct
  written word of its edition, plus lowercased forms and hyphen words split joined:

  | Bundle | Words | Different |
  |---|---|---|
  | language-analysis fe645bf6 zin/aa440920 | 21,999 | 0 |
  | language-analysis b7b758a0 zin/d33c99ab | 22,011 | 0 |
  | language-analysis ab1298c2 kvg/d8fdb53e | 27,014 | 0 |
  | language-analysis 8fec0cf0 kvg/2cd8a4bc | 15,837 | 0 |
  | language-analysis fe645bf6 kvg/3d2dd099 | 15,837 | 0 |
  | language-analysis b7b758a0 kvg/b54d7f20 | 27,014 | 0 |

  (The second Kuni count includes hyphen words. Tool: `pipeline/scripts/tools/check_split_engine.py`.)
