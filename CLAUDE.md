# CLAUDE.md — SpectralRankingGlobal

## Project root
`/home/lc/Projects/SpectralRankingGlobal`

## Purpose
Generalised spectral ranking of sources and institutions across all fields.
Rankings are always over sources {S} and institutions {I}. Set type controls
only how the corpus is filtered (by seed journals, or by OA subfield topics).
See NEW_PROJECT_DESIGN.md for the full design rationale.

## Python environment
Always use `.venv/bin/python` and `.venv/bin/pip`. Never invoke bare `python` or `pip`.

## Machine-specific config
`config.yaml` (gitignored) — three keys: `PROJECT_ROOT`, `WORKING`, `OPENALEX`.
Read via `util.load_config()` → `Paths`. Never inline yaml loading.

## Set types
Two parallel set types, identical downstream pipeline. Both rank sources {S} and
institutions {I}; set type controls corpus filtering only:
- `journal`: corpus = works in seed journals {S}; filter is `work.source_id IN {S}`
- `topic`: corpus = works tagged with topics in a field; filter via `topics WHERE field_idx = X`

Topic-type sets are auto-generated from the 26 OA fields (`field_idx` 11–36).
No explicit set config is needed for topic sets; only runs need a config file.

## Run parameters (`util.Run` dataclass)
- `window`: `{census_start}_{census_end}` e.g. `2020_2024`
- `field_idx`: OA field index 11–36
- `tau_s`, `tau_u`: retention thresholds in **weighted works per year**
  (actual cutoff = tau × window_years applied to candidacy totals)
- `m`: block mask `(m_SS, m_SI, m_IS, m_II)`; `(0,1,1,0)` = bipartite (standard)
- `alpha`: Katz damping; `1.0` = pure Perron eigenvector
- `rho`: `0` = fixed-count (R̄/Rᵢ); `1` = full-count
- `chi`: source–institution mixing; `-1` = χ* = Nᵤ/(Nₛ+Nᵤ); only for `m=(1,1,1,1)`
- `mu_type`: `''` for `alpha=1`; `'uniform'` or `'unit_scaled'` for `alpha<1`

## Institution types included
`('education', 'nonprofit', 'government', 'healthcare', 'other')`
Excludes: company, funder, archive. Healthcare was added to capture clinical
research institutions (MSK, Mayo, Kaiser, Ragon, etc.) which OA classifies
separately from education/nonprofit.

## Folder structure
```
SpectralRankingGlobal/
  pipeline/                  # all pipeline stages + ranking engine
    build_flat_works.py      #   ✅ stage 1a/1b: flat works + corpus references
    build_field_candidacy.py #   ✅ stage 2: per-field source/institution candidacy
    build_edge_list_field.py #   ✅ stage 3: field citation edge list
    build_csr_field.py       #   ✅ stage 4a: parquet edge list → CSRData
    run_rankings.py          #   ✅ stage 4b: orchestrate all fields
    show_rankings.py         #   display rankings with OA names
    katz_ranker.py           #   spectral algorithms (Perron/Katz, bipartite) — pure math
    summary_flat_works.py    #   diagnostic summary of flat_works
    tests/
  spectral_analysis/         # next: eigenvector community structure
  util/
    load_config.py           #   load_config() → Paths, load_runs() → list
    runs.py                  #   Run dataclass
  ZARCHIVE/                  # superseded EconomicsBusiness code
  config.yaml                # machine-specific paths (gitignored)
  requirements.txt
  NEW_PROJECT_DESIGN.md      # design rationale
```

## Data persistence
```
OPENALEX/parquet/
  works/*.parquet            OA snapshot — works
  authorships/*.parquet      OA snapshot — authorships
  topics/*.parquet           OA snapshot — work×topic×score + full hierarchy
  sources.parquet            OA snapshot — source metadata
  institutions.parquet       OA snapshot — institution metadata
  references/*.parquet       OA snapshot — (citer_idx, cited_idx)

WORKING/
  flat_works_{ymin}_{ymax}.parquet              ✅ stage 1a; ~112M rows
                                                  (work×institution×field, with weights)
                                                  institution types: edu+nonprofit+gov+healthcare+other
  corpus_references_{ymin}_{ymax}.parquet       ✅ stage 1b; ~358M rows
                                                  (citer_idx, cited_idx) both in flat_works
  field_source_cands_{window}.parquet           ✅ stage 2; source weighted works per field
  field_inst_cands_{window}.parquet             ✅ stage 2; inst weighted works per field
  el_{field_idx}_{window}_tauS{s}_tauU{u}.parquet  ✅ stage 3; one per field (~2-20s each)
  rankings_{field_idx}_{window}.parquet         ✅ stage 4; all 26 fields complete
  rankings_{field_idx}_{window}_diag.json       ✅ stage 4; diagnostics per field
```
`window` = `{census_start}_{census_end}` (e.g. `2020_2024`).

## Pipeline status — window 2020_2024
- ✅ Stage 1a: `build_flat_works.py` — flat_works_2016_2025.parquet (~112M rows, incl. healthcare)
- ✅ Stage 1b: `build_flat_works.py` — corpus_references_2016_2025.parquet (~358M rows, 115s)
- ✅ Stage 2: `build_field_candidacy.py` — candidacy parquets for 2020_2024
- ✅ Stage 3: `build_edge_list_field.py` — edge list construction (~2s/field with corpus_refs)
- ✅ Stage 4a: `build_csr_field.py` — CSR matrix assembly
- ✅ Stage 4b: `run_rankings.py` — all 26 fields ranked (window 2020_2024)

## Key bugs fixed
- **Source candidacy overcounting**: `flat_works` has one row per (work×institution×field),
  so naïve `SUM(field_weight)` for sources counted field_weight once per institution per work.
  Fixed with `SELECT DISTINCT work_idx, source_idx, field_idx, field_weight` before summing.
  Institution candidacy (`SUM(field_weight * inst_weight)`) was correct.

## Field index mapping (OA → CWTS Leiden)
26 OA fields, `field_idx` 11–36. OA Domain is OpenAlex's own grouping; CWTS Leiden Main Field is the Leiden Ranking grouping used for cross-field comparisons.

| field_idx | OA Field                                      | OA Domain        | CWTS Leiden Main Field                  |
|-----------|-----------------------------------------------|------------------|-----------------------------------------|
| 11        | Agricultural and Biological Sciences          | Life Sciences    | 3. Life and Earth Sciences              |
| 12        | Arts and Humanities                           | Social Sciences  | 5. Social Sciences and Humanities       |
| 13        | Biochemistry, Genetics and Molecular Biology  | Life Sciences    | 3. Life and Earth Sciences              |
| 14        | Business, Management and Accounting           | Social Sciences  | 5. Social Sciences and Humanities       |
| 15        | Chemical Engineering                          | Physical Sciences| 2. Physical Sciences and Engineering    |
| 16        | Chemistry                                     | Physical Sciences| 2. Physical Sciences and Engineering    |
| 17        | Computer Science                              | Physical Sciences| 1. Mathematics and Computer Science     |
| 18        | Decision Sciences                             | Social Sciences  | 5. Social Sciences and Humanities       |
| 19        | Earth and Planetary Sciences                  | Physical Sciences| 3. Life and Earth Sciences              |
| 20        | Economics, Econometrics and Finance           | Social Sciences  | 5. Social Sciences and Humanities       |
| 21        | Energy                                        | Physical Sciences| 2. Physical Sciences and Engineering    |
| 22        | Engineering                                   | Physical Sciences| 2. Physical Sciences and Engineering    |
| 23        | Environmental Science                         | Physical Sciences| 3. Life and Earth Sciences              |
| 24        | Immunology and Microbiology                   | Life Sciences    | 3. Life and Earth Sciences              |
| 25        | Materials Science                             | Physical Sciences| 2. Physical Sciences and Engineering    |
| 26        | Mathematics                                   | Physical Sciences| 1. Mathematics and Computer Science     |
| 27        | Medicine                                      | Health Sciences  | 4. Biomedical and Health Sciences       |
| 28        | Neuroscience                                  | Life Sciences    | 4. Biomedical and Health Sciences       |
| 29        | Nursing                                       | Health Sciences  | 4. Biomedical and Health Sciences       |
| 30        | Pharmacology, Toxicology and Pharmaceutics    | Life Sciences    | 4. Biomedical and Health Sciences       |
| 31        | Physics and Astronomy                         | Physical Sciences| 2. Physical Sciences and Engineering    |
| 32        | Psychology                                    | Social Sciences  | 5. Social Sciences and Humanities       |
| 33        | Social Sciences                               | Social Sciences  | 5. Social Sciences and Humanities       |
| 34        | Veterinary                                    | Health Sciences  | 4. Biomedical and Health Sciences       |
| 35        | Dentistry                                     | Health Sciences  | 4. Biomedical and Health Sciences       |
| 36        | Health Professions                            | Health Sciences  | 4. Biomedical and Health Sciences       |

## Spectral gap by field (window 2020_2024, bipartite m=(0,1,1,0))
Small gap = near-reducible internal structure (sub-communities); large gap = unified hierarchy.
The bipartite walk blends through institutions — only joint source+institution co-clusters
survive as eigenvector signal. The second eigenvector reveals the dominant partition when gap is small.

| field_idx | Field                                  | gap   |
|-----------|----------------------------------------|-------|
| 11        | Agricultural and Biological Sciences   | 0.653 |
| 12        | Arts and Humanities                    | 0.117 |
| 13        | Biochemistry, Genetics and Mol. Bio.   | 0.748 |
| 14        | Business, Management and Accounting    | 0.091 |
| 15        | Chemical Engineering                   | 0.817 |
| 16        | Chemistry                              | 0.724 |
| 17        | Computer Science                       | 0.439 |
| 18        | Decision Sciences                      | 0.530 |
| 19        | Earth and Planetary Sciences           | 0.587 |
| 20        | Economics, Econometrics and Finance    | 0.149 |
| 21        | Energy                                 | 0.791 |
| 22        | Engineering                            | 0.210 |
| 23        | Environmental Science                  | 0.722 |
| 24        | Immunology and Microbiology            | 0.653 |
| 25        | Materials Science                      | 0.793 |
| 26        | Mathematics                            | 0.332 |
| 27        | Medicine                               | 0.648 |
| 28        | Neuroscience                           | 0.860 |
| 29        | Nursing                                | 0.435 |
| 30        | Pharmacology, Toxicology               | 0.548 |
| 31        | Physics and Astronomy                  | 0.751 |
| 32        | Psychology                             | 0.801 |
| 33        | Social Sciences                        | 0.351 |
| 34        | Veterinary                             | 0.110 |
| 35        | Dentistry                              | 0.856 |
| 36        | Health Professions                     | 0.288 |

## TODO — next: spectral_analysis/
- Compute top-k eigenvectors for small-gap fields (Arts, Business, Econ, Vet, Engineering, Maths)
- Embed sources+institutions in eigenvector space → identify co-clusters / subfield structure
- The second eigenvector sign-pattern partitions the field into its two dominant sub-communities

## TODO — Leiden main field rankings
- Aggregate the 26 OA fields into the 5 CWTS Leiden Main Fields (see field index mapping above)
  and run spectral rankings at that coarser level; analyse how parameter choices (tau, alpha, rho, chi)
  affect institution and source rankings relative to per-OA-field results

## TODO — Australian HEP heatmap
- Obtain the official TEQSA/DESE table of Australian Higher Education Providers (HEPs)
- Match HEPs to OA institutions by name/ROR
- Build a heatmap: rows = HEPs, columns = fields (or Leiden main fields), colour = rank percentile
  sorted so that highest-ranked HEPs appear at the bottom-right (ascending rank, descending field score)

## TODO — Subfield rankings
- Investigate running spectral ranking at the OA subfield level (below the 26 fields)
  to expose finer disciplinary structure; assess corpus sparsity vs. spectral signal trade-off

## TODO — Parameter audit and unimplemented flags
Three binary flags exist in `Run` and `runs.yaml` but are not yet wired into the pipeline.
All three require changes to `build_edge_list_field.py` and `build_csr_field.py`.

### ε — epsilon (cross-boundary sentinel, default 0)
- ε=0: standard edge list (only within-field citations)
- ε=1: add dummy sentinel units (`source_idx=1`, `institution_idx=1`) to absorb cross-field
  citations; their v-scores are masked to NaN after ranking so they don't appear in output.
  Origin: `EconomicsBusiness/spectral_ranking/build_csr.py` `SX_IDX`/`IX_IDX` logic.
- TODO: port sentinel-edge injection into `build_edge_list_field.py`; add `is_sentinel_s`/
  `is_sentinel_u` masks to `CSRData`; update `katz_ranker.py` to mask NaN on output.

### ω — omega (institution weighting mode, default 0)
- ω=0: **author-fractional** weighting — institution credit proportional to author share
  (`inst_weight / cited_inst_weight` columns from `flat_works`)
- ω=1: **direct 1/N_inst** weighting — equal credit per affiliated institution
  (`direct_inst_weight / direct_cited_inst_weight` columns, not yet in `flat_works`)
- TODO: add `direct_inst_weight` and `direct_cited_inst_weight` columns to `build_flat_works.py`;
  wire the column selection into `build_csr_field.py` based on `run.omega`.

### β — beta (unit self-reference exclusion, default 0)
- β=0: self-references included (baseline)
- β=1: exclude edges where `citer_source_idx == cited_source_idx`
  AND `citer_inst_idx == cited_inst_idx` (zeroes the diagonal of C_SS and C_II)
- TODO: add `WHERE` filter in `build_csr_field.py` `_tmp_el` materialisation when `run.beta == 1`.
