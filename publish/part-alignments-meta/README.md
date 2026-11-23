---
license: cc-by-4.0
pretty_name: Part alignments — provenance sidecar
tags:
- bible
- alignment
- word-alignment
- morphology
---

# part-alignments-meta — provenance sidecar

An optional, additive layer of [`part-alignments`](https://huggingface.co/datasets/bcv-commons/part-alignments). It holds ONLY the
`<BOOK>_<fp8>.meta.json` files, under the **same relative path** as the main files in the main repo:

```
<iso[0]>/<iso>/<edition>/<BOOK>_<fp8>.meta.json
```

For `f/fra/fra_lsg/MRK_1a2b3c4d.json` in the main repo, this layer's file is `f/fra/fra_lsg/MRK_1a2b3c4d.meta.json` here. Not every
book has this layer; a missing file is a 404, not an error.

Content: the per-unit provenance channels: method, conf, contested, bonus, rule, rend, parts (target word parts) and runs, each position-parallel to the book's index (see the main repo's README). Download it into the same folder as the main files and the main repo's `fold.py` / `fold.mjs` use it.

Licence: CC BY 4.0, as the main repo (its README, "Licences").
