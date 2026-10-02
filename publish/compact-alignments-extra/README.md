---
pretty_name: Compact alignments — opt-in residual layer
tags:
  - bible
  - word-alignment
  - interlinear
license: cc0-1.0
---

# compact-alignments-extra — opt-in residual layer

An optional, additive layer of [`compact-alignments`](https://huggingface.co/datasets/bcv-commons/compact-alignments). It holds ONLY the
`<BOOK>_<hash>.extra.json` files, under the **same relative path** as in the main repo:

```
<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.extra.json
```

For an alignment array at `a/arb/arb_vdv/GEN_1a2b3.json` in the main repo, this layer's file is `a/arb/arb_vdv/GEN_1a2b3.extra.json` here. The `<hash>`
is the main file's content hash, so the two always belong together. Not every book has this layer; a missing file is a 404, not an error.

Content: the same compact string format as the main array, for target words a second, residual alignment pass could still explain after
eflomal, gloss, span extension and gap-fill. It is deliberately NOT merged into the main array. Format: see the main repo's README (the `r` method
letter).

Everything else — the README that defines the formats, `manifest.json`, `_index/`, `tokenize.js`, the decisions ledger — stays in the main repo. The
authoritative list of editions and books is its `manifest.json`. The layer was split out because the single repo had passed Hugging Face's
recommended 100,000 files.

## License

CC0-1.0, like the main dataset. Each edition's own source and licence pointer is in the main repo's `manifest.json`.
