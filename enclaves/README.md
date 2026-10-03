# Enclaves Subsystem

This directory contains the pipeline, analysis tools, generated reports, and visualizations for identifying and characterizing **citation enclaves** (low-prestige venues with disproportionately high intra-cluster citations) across all 26 OpenAlex fields.

For the mathematical background and manuscript draft, see [latex/enclaves/sections/04_enclaves.tex](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/latex/enclaves/sections/04_enclaves.tex). For pipeline data schemas, see [REFERENCE.md](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/REFERENCE.md).

---

## 1. Pipeline Stages & Execution Order

The enclave workflow consists of five sequential stages plus visualization:

```
Stage E1: build_enclave_hcw.py       -> enclave_hcw_{window}_{label}.parquet
Stage E2: tfidf_enclave.py           -> enclave_tfidf_{window}_{label}.parquet
Stage E3: nmf_enclave.py             -> enclave_nmf_{window}_{label}.parquet
Stage E4: network_enclave.py         -> enclaves/reports/{fid}_{window}_{label}.md (26 files)
Stage E5: researcher_enclaves.py     -> enclaves/reports/{fid}_{window}_{label}_researchers.md (26 files)
Plots:    plot_hca_hcr.py            -> enclaves/plots/*.pdf (6 PDFs across 5 broad areas + all)
```

### Stage Details

1. **E1: Highly Cited Work (HCW) Selection** ([build_enclave_hcw.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/build_enclave_hcw.py))
   - Identifies works in the 99th percentile of intra-corpus citations (`n_intra`) per publication year (2014–2023).
   - Labels each work with source spectral score ($v$) and mean citing score ($\langle v \rangle$).
   - Isolates the **HCW--** quadrant: works published in low-prestige venues ($v < 1$) cited predominantly by low-prestige venues ($\langle v \rangle < 1$).

2. **E2: TF-IDF Vocabulary Scoring** ([tfidf_enclave.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/tfidf_enclave.py))
   - Extracts unigram and bigram term frequencies across titles and abstracts of HCW-- works.
   - Computes term lift relative to the background corpus.

3. **E3: NMF Topic Modeling & AI Naming** ([nmf_enclave.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/nmf_enclave.py))
   - Fits Non-Negative Matrix Factorization (adaptive $k = \min(10, n // 20)$) to partition HCW-- into cohesive topic chambers.
   - Uses `--auto-name-field` with LLM integration to merge redundant topics and generate concise ($\le 3$ words) enclave names.

4. **E4: 1-Hop Citation Networks** ([network_enclave.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/network_enclave.py))
   - Builds citation subgraphs seeded by HCW-- works for each named enclave.
   - Computes network metrics: component counts, seed-to-seed loop citation percentages (`loop_pct`), largest component share (`lg_pct`), and top publishing sources.
   - Emits 26 field network reports to [enclaves/reports/](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/reports/).

5. **E5: Researcher & Author Profiling** ([researcher_enclaves.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/researcher_enclaves.py))
   - Maps authors and institutional affiliations to each enclave network.
   - Identifies top contributing researchers and author concentration.
   - Emits 26 field researcher reports to [enclaves/reports/](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/reports/).

6. **Visualization: HCA/HCR Overlay** ([plot_hca_hcr.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/plot_hca_hcr.py))
   - Plots citer influence vs. cited influence across the 5 canonical broad research areas defined in [util/areas.py](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/util/areas.py) (`MCS`, `PSE`, `LES`, `BHS`, `SSH`).
   - Overlays Highly Cited Authors (HCA) and Clarivate Highly Cited Researchers (HCR).

---

## 2. Generated Outputs & Catalog

* **Network Reports** ([enclaves/reports/{fid}_2020_2024_baseline.md](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/reports/)):
  - Contain executive summaries, topic breakdowns, citation density tables, and component analyses for all 26 fields (e.g., [Field 17 Computer Science](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/reports/17_2020_2024_baseline.md), [Field 26 Mathematics](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/reports/26_2020_2024_baseline.md)).
* **Researcher Reports** ([enclaves/reports/{fid}_2020_2024_baseline_researchers.md](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/reports/)):
  - Detail frequent authors, institutional ties, and author co-appearance patterns per enclave.
* **Area Plots** ([enclaves/plots/](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/plots/)):
  - `enclave_citer_v_2020_2024_baseline_L1_MathCS.pdf`
  - `enclave_citer_v_2020_2024_baseline_L2_PhysEng.pdf`
  - `enclave_citer_v_2020_2024_baseline_L3_LifeEarth.pdf`
  - `enclave_citer_v_2020_2024_baseline_L4_BiomedHealth.pdf`
  - `enclave_citer_v_2020_2024_baseline_L5_SocialHum.pdf`
  - `enclave_citer_v_2020_2024_baseline_all.pdf`
* **Intermediate Working Parquet** (stored in `WORKING` directory):
  - `enclave_hcw_2020_2024_baseline.parquet`
  - `enclave_tfidf_2020_2024_baseline.parquet`
  - `enclave_nmf_2020_2024_baseline.parquet`
  - `hcw_authorships.parquet`

---

## 3. Downstream Research & Analysis Directions

When conducting further research or drafting manuscripts, the enclave subsystem supports four main operational paths:

1. **Disciplinary Deep-Dives**:
   - Focus on specific fields where enclave behavior is most pronounced (e.g., Mathematics, Environmental Science, Materials Science).
   - Trace the top journals driving high `loop_pct` (seed-to-seed citation feedback).

2. **Manuscript Figure & Table Generation**:
   - Export and integrate the 5 broad area vector PDFs from [enclaves/plots/](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/enclaves/plots/) into [latex/enclaves/](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/latex/enclaves/).
   - Compile summary tables of enclave volume, loop percentages, and top publishers for Section 4 of the paper.

3. **Sensitivity & Threshold Testing**:
   - Test HCW sensitivity by varying the percentile cutoff (e.g., 95th or 99.5th percentile in `build_enclave_hcw.py`).
   - Adjust NMF topic granularities ($k$) or fine-tune LLM prompt naming constraints in `nmf_enclave.py`.

4. **Author & Institutional Concentration Analysis**:
   - Cross-reference enclave authors with Clarivate Highly Cited Researchers (HCR) and national affiliation datasets (e.g., Australian Higher Education Provider data).
   - Evaluate whether certain enclaves are localized within specific institutions or international research blocs.

---

## 4. Verification & Testing

To verify enclave data freshness:
```bash
.venv/bin/python pipeline/pipeline_status.py
```

To run the enclave test suite:
```bash
.venv/bin/python -m pytest enclaves/tests --import-mode=importlib
```
