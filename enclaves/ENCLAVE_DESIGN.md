# Enclave Analysis Pipeline — Design

## Overview

For a selected field, identify named topic clusters ("enclaves") within the
low-prestige highly-cited works, then characterise the citation network of each enclave.

---

## Stage 1 — HCW selection and v-score labelling

For each publication year y in 2014–2023, for the chosen field:
- Count intra-corpus citations received by each work (`n_intra`).
- Retain works in the top 1% by `n_intra` → **HCW**.

Label each HCW with:
- `v`  = source_v (spectral score of its publishing source)
- `<v>` = mean_citer_v (mean spectral score of works that cite it)

Classify each HCW into one of four quadrants:

| quadrant | condition                    | meaning                              |
|----------|------------------------------|--------------------------------------|
| HCW++    | v ≥ 1  AND  <v> ≥ 1         | high-prestige work, cited by high-prestige |
| HCW+-    | v ≥ 1  AND  <v> < 1         | high-prestige work, cited by low-prestige  |
| HCW-+    | v < 1  AND  <v> ≥ 1         | low-prestige work, cited by high-prestige  |
| HCW--    | v < 1  AND  <v> < 1         | low-prestige work, cited by low-prestige   |

**Target population: HCW--**

*Current code*: `build_enclave_hcw.py` ✅ — builds HCW with `source_v` and
`mean_citer_v` attached; years 2014–2023.

---

## Stage 2 — TF-IDF + NMF on HCW--

**Input**: titles of all HCW-- in the field.

- Compute TF-IDF on unigrams + bigrams (English stop list + domain list).
- Fit NMF with k adaptive topics (k = min(10, n // 20), min n = 30).
- Assign each HCW-- to its argmax topic → raw **echo chambers**.

*Current code*: `nmf_enclave.py` ✅ — runs on `source_v < 1 AND mean_citer_v < 1`.

---

## Stage 3 — AI consolidation and naming

**Input**: top TF-IDF terms per echo chamber.

Send to Gemini API; model returns JSON with:
- `merges`: lists of topic indices to collapse into one enclave.
- `names`: ≤3-word name per resulting enclave.

Apply merges and names → each HCW-- now carries a named **enclave** assignment.

*Current code*: `nmf_enclave.py --auto-name-field` ✅ — calls Gemini, applies
merges + names to the NMF parquet.

---

## Stage 4 — 1-hop citation network **per enclave**

For each named enclave E:

- **Seed nodes**: work_idx of HCW-- in E.
- **1-hop expansion**: all retained corpus works that cite any seed.
- **Edges**: (citer → seed) plus any (seed → seed) loop edges.
- Build connected components (union-find).

Metrics per enclave:
| metric    | meaning                                         |
|-----------|-------------------------------------------------|
| n_hcw     | HCW-- in enclave                                |
| n_citers  | distinct 1-hop citing works                     |
| loop_pct  | % of edges that are HCW-- → HCW-- (seed→seed)  |
| n_comp    | connected components                            |
| lg_hcw    | HCW-- in the largest component                  |
| lg_pct    | lg_hcw / n_hcw                                  |
| top_srcs  | top journals by HCW-- count                     |

*Current code*: `network_enclave.py` ❌ — builds **one network per field**
seeded from ALL HCW-- at once, ignoring NMF/enclave assignments entirely.
**This is the missing step.**

---

## Stage 5 — Markdown report

One `.md` file per (field, label):

```
# Enclave network report — {field_name} ({window}, {label})

## Summary

| Enclave                | n_hcw | citers   | loop% | n_comp | lg_hcw | lg%  |
|------------------------|-------|----------|-------|--------|--------|------|
| Fractional Calculus    |   392 |  xx,xxx  |  2.1% |      3 |    385 |  98% |
| Nonlinear Waves        |   202 |  xx,xxx  |  0.8% |      5 |    195 |  97% |
| ...                    |       |          |       |        |        |      |

## Enclave detail

### Fractional Calculus (n=392)
- Components: 3
- Top sources: 75×Symmetry  71×Mathematics  54×Fractal and Fractional ...
- Comp 1: hcw=385, citers=xx,xxx, top_src=...
- Comp 2: hcw=5, citers=xx, ...
```

---

## Status vs current code

| Stage | What is needed | Current code | Status |
|-------|---------------|--------------|--------|
| 1 | HCW selection + quadrant labels | `build_enclave_hcw.py` | ✅ done |
| 2 | NMF on HCW-- | `nmf_enclave.py` | ✅ done |
| 3 | AI naming / merging | `nmf_enclave.py --auto-name-field` | ✅ done |
| 4 | 1-hop network **per enclave** | `network_enclave.py` | ❌ needs rewrite |
| 5 | MD report per field | — | ❌ not yet |
