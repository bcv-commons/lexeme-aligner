---
license: cc-by-4.0
pretty_name: Part alignments — residual layer
tags:
- bible
- alignment
- word-alignment
- morphology
---

# part-alignments-extra — residual layer

An optional, additive layer of [`part-alignments`](https://huggingface.co/datasets/bcv-commons/part-alignments). It holds ONLY the
`<BOOK>_<fp8>.extra.json` files, under the **same relative path** as the main files in the main repo:

```
<iso[0]>/<iso>/<edition>/<BOOK>_<fp8>.extra.json
```

For `f/fra/fra_lsg/MRK_1a2b3c4d.json` in the main repo, this layer's file is `f/fra/fra_lsg/MRK_1a2b3c4d.extra.json` here. Not every
book has this layer; a missing file is a 404, not an error.

Content: the opt-in residual layer: links for units the main array leaves out, position-parallel to the book's index. Download it into the same folder as the main files and the main repo's `fold.py` / `fold.mjs` use it.

Licence: CC BY 4.0, as the main repo (its README, "Licences").
