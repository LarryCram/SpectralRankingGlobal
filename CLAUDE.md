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
- `field_idx`: grouping index — OA field 11–36, Leiden group 1–5, or OA subfield 1100–3616
  - `run.is_leiden` = True when field_idx ∈ 1–5; `run.is_subfield` = True when field_idx ≥ 1000
  - `sc_path`/`ic_path` auto-select the right candidacy file for the level
- `tau_s`, `tau_u`: retention thresholds in **weighted works per year**
  (actual cutoff = tau × window_years applied to candidacy totals)
  Baseline: τ_s = τ_u = 10/yr → 50 w.w. over the 5-year window
- `m`: block mask `(m_SS, m_SI, m_IS, m_II)`; `(0,1,1,0)` = bipartite (standard)
- `alpha`: Katz damping; `1.0` = pure Perron eigenvector
- `rho`: `0` = fixed-count (R̄/Rᵢ); `1` = full-count
- `chi`: source–institution mixing; `-1` = χ* = Nᵤ/(Nₛ+Nᵤ); only for `m=(1,1,1,1)`
- `mu_type`: `''` for `alpha=1`; `'uniform'` or `'unit_scaled'` for `alpha<1`
- `label`: string encoded in all output filenames (e.g. `baseline`, `tau20`, `OECDG20CIA`)
- `bloc`: key into `GlobalSettings.blocs`; `''` = all countries

## Institution types included
`('education', 'nonprofit', 'government', 'healthcare', 'other')`
Excludes: company, funder, archive. Healthcare was added to capture clinical
research institutions (MSK, Mayo, Kaiser, Ragon, etc.) which OA classifies
separately from education/nonprofit.

## Folder structure
```
SpectralRankingGlobal/
  pipeline/                    # all pipeline stages + ranking engine
    build_flat_works.py        #   ✅ stage 1a/1b: flat works + corpus references
    build_field_candidacy.py   #   ✅ stage 2: per-field/leiden/subfield candidacy
    build_edge_list_field.py   #   ✅ stage 3: field citation edge list
    build_csr_field.py         #   ✅ stage 4a: parquet edge list → CSRData
    run_rankings.py            #   ✅ stage 4b: rank one field
    run_leiden_sensitivity.py  #   ✅ parameter sensitivity suite for Leiden
    run_leiden_bloc.py         #   ✅ country-bloc Leiden rankings
    run_field_bloc.py          #   ✅ country-bloc OA field rankings
    show_rankings.py           #   display rankings with OA names
    katz_ranker.py             #   spectral algorithms (Perron/Katz, bipartite) — pure math
    summary_flat_works.py      #   diagnostic summary of flat_works
    tests/
  analysis/
    leiden_facets.py           #   ✅ 2×5 log(v) vs rank, multi-label overlay
    leiden_bloc_facets.py      #   ✅ baseline vs one comparison per PDF (7 PDFs)
    hep_heatmap.py             #   ✅ AU HEP influence heatmap across 26 fields
    impact_facets.py           #   🔄 in development
  enclaves/
    build_enclave_hcw.py       #   ✅ HCW detection + v attachment (stages E1)
    tfidf_enclave.py           #   ✅ TF-IDF term lift per field (stage E2)
    nmf_enclave.py             #   ✅ NMF topic clustering per field (stage E3)
  spectral_analysis/           # next: eigenvector community structure
  util/
    load_config.py             #   load_config() → Paths, load_runs() → list
    runs.py                    #   Run dataclass + GlobalSettings + LEIDEN_GROUPS
  data/
    bloc.xlsx                  #   bloc_name → ISO-3166-1-alpha-2 country codes
    HEP_concordances.xlsx      #   AU HEP name → institution_idx mapping
  ZARCHIVE/                    # superseded EconomicsBusiness code
  config.yaml                  # machine-specific paths (gitignored)
  requirements.txt
  NEW_PROJECT_DESIGN.md        # design rationale
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
  flat_works_2016_2025.parquet              ✅ stage 1a; 153.5M rows
                                              (work×institution×subfield, with weights)
                                              cols: work_idx, publication_year,
                                                source_idx, institution_idx, country_code,
                                                inst_weight, direct_inst_weight,
                                                subfield_idx, subfield_name,
                                                field_idx, field_weight,
                                                leiden_idx, leiden_name,
                                                referenced_works_count
  corpus_references_2016_2025.parquet       ✅ stage 1b; ~358M rows
                                              (citer_idx, cited_idx) both in flat_works
  field_source_cands_{window}.parquet       ✅ stage 2; source weighted works per OA field
  field_inst_cands_{window}.parquet         ✅ stage 2; inst weighted works per OA field
  leiden_source_cands_{window}.parquet      ✅ stage 2; source weighted works per Leiden group
  leiden_inst_cands_{window}.parquet        ✅ stage 2; inst weighted works per Leiden group
  subfield_source_cands_{window}.parquet    ✅ stage 2; source weighted works per subfield
  subfield_inst_cands_{window}.parquet      ✅ stage 2; inst weighted works per subfield
  el_{field_idx}_{window}_{label}.parquet   ✅ stage 3; one per (field, label)
  rankings_{field_idx}_{window}_{label}.parquet      ✅ stage 4; ranked units
  rankings_{field_idx}_{window}_{label}_diag.json    ✅ stage 4; diagnostics
  enclave_hcw_{window}_{label}.parquet     ✅ stage E1; HCW with v scores + titles
                                             cols: field_idx, work_idx, publication_year,
                                               source_idx, n_intra, year_threshold,
                                               source_v, mean_inst_v, mean_citer_v,
                                               n_citer_hi, n_citer_lo, mean_inst_v, gap, title
  enclave_tfidf_{window}_{label}.parquet   ✅ stage E2; enclave-distinctive TF-IDF terms
  enclave_nmf_{window}_{label}.parquet     ✅ stage E3; NMF topic assignments per enclave work
```
`window` = `{census_start}_{census_end}` (e.g. `2020_2024`).
`field_idx` = OA field 11–36, Leiden group 1–5, or subfield ≥ 1000.

## Pipeline status — window 2020_2024
- ✅ Stage 1a: `build_flat_works.py` — flat_works_2016_2025.parquet (153.5M rows, subfield granularity)
- ✅ Stage 1b: `build_flat_works.py` — corpus_references_2016_2025.parquet (~358M rows)
- ✅ Stage 2: `build_field_candidacy.py` — all 6 candidacy parquets (field/leiden/subfield × source/inst)
- ✅ Stage 3+4 baseline: `run_field_bloc.py` — all 26 OA fields (label=`baseline`)
- ✅ Stage 3+4 baseline: `run_leiden_bloc.py` — all 5 Leiden groups (label=`baseline`)
- ✅ Sensitivity suite: `run_leiden_sensitivity.py` — 5 variants × 5 Leiden groups
- 🔄 Bloc suite: `run_leiden_bloc.py` — OECDG20CIA, CIAA (in progress / pending)

## Sensitivity variants (Leiden, window 2020_2024)
Five one-at-a-time parameter variants against the baseline (τ=10, ρ=0, ε=0, ω=0, β=0):

| label   | change        | needs new edge list? |
|---------|---------------|----------------------|
| tau20   | τ_s=τ_u=20/yr | yes                  |
| rho1    | ρ=1           | no (symlink)         |
| eps1    | ε=1           | yes                  |
| om1     | ω=1           | no (symlink)         |
| beta1   | β=1           | no (symlink)         |

`run_leiden_sensitivity.py` uses symlinks for no-el variants to avoid duplicating
5–9 GB edge lists for Leiden 3/4. Fresh DuckDB connection per leiden group prevents
memory accumulation across large edge list builds.

## Bloc runs (Leiden, window 2020_2024)
All-in filter: a work is included only if EVERY affiliated institution is in the bloc.
Candidacy parquets are global (shared with baseline); new edge lists built per bloc.

| label      | bloc key     | countries |
|------------|--------------|-----------|
| OECDG20CIA | OECDG20-CIA  | 43        |
| CIAA       | CIAA         | 4 (AU,CN,IN,US) |

## Analysis outputs
- `analysis/leiden_facets.pdf` — 2×5 log(v) vs rank; multi-label overlay, default all 6 labels
- `analysis/leiden_{label}.pdf` — 7 PDFs from `leiden_bloc_facets.py`, one per comparison:
  - `leiden_OECDG20CIA.pdf`, `leiden_CIAA.pdf` — bloc vs baseline (coloured scatter)
  - `leiden_tau20.pdf`, `leiden_rho1.pdf`, `leiden_eps1.pdf`, `leiden_om1.pdf`, `leiden_beta1.pdf` — variants vs baseline (green scatter)
- `analysis/hep_heatmap.pdf` — AU HEP institution scores across 26 OA fields

## Key bugs fixed
- **Source candidacy overcounting**: `flat_works` has one row per (work×institution×subfield),
  so naïve `SUM(field_weight)` for sources counted field_weight once per institution per work.
  Fixed with `SELECT DISTINCT work_idx, source_idx, field_idx, field_weight` before summing.
  Institution candidacy (`SUM(field_weight * inst_weight)`) was correct.
- **Stale unlabeled ranking files**: runners now write `rankings_{fid}_{window}_{label}.parquet`;
  old unlabeled files were deleted manually after the field runner was updated.
- **DuckDB segfault on large Leiden runs**: using a single connection across all leiden groups
  caused memory accumulation → segfault on leiden 2 (39M citation pairs). Fixed by opening
  a fresh connection per leiden group in the sensitivity and bloc runners.
- **Corrupt edge list from crashed run**: if a build crashes mid-write, the parquet file exists
  but has no magic bytes. Detection: `duckdb.execute("SELECT COUNT(*) FROM 'file'")` raises
  `InvalidInputException`. Fix: delete and re-run.

## Field index mapping (OA → CWTS Leiden)
26 OA fields, `field_idx` 11–36.

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

## Spectral gap by field (window 2020_2024, bipartite m=(0,1,1,0), label=baseline)
Small gap = near-reducible internal structure (sub-communities); large gap = unified hierarchy.
The bipartite walk blends through institutions — only joint source+institution co-clusters
survive as eigenvector signal. The second eigenvector reveals the dominant partition when gap is small.

### OA fields (field_idx 11–36)
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

### Leiden groups (field_idx 1–5)
| field_idx | Leiden Group                          | n_s    | n_u    | gap   |
|-----------|---------------------------------------|--------|--------|-------|
| 1         | Mathematics and Computer Science      | 3,624  | 3,754  | 0.420 |
| 2         | Physical Sciences and Engineering     | 6,724  | 5,694  | 0.298 |
| 3         | Life and Earth Sciences               | 8,475  | 7,008  | 0.572 |
| 4         | Biomedical and Health Sciences        | 11,585 | 10,069 | 0.423 |
| 5         | Social Sciences and Humanities        | 16,082 | 7,251  | 0.455 |

## HCW analysis (enclaves/ pipeline)

Identifies Highly Cited Works (HCW) with below-average unit scores — works that punch
above their citation weight relative to their publishing context.

### Definitions
- **HCW**: top 1% of `n_intra` (retained-corpus citation count) per `(publication_year, field_idx)`.
  Year range: 2016–2024. Rankings window: 2020_2024.
- **n_intra**: count of citations received from any retained work in ANY field
  (cross-field citations included; citer v = MAX source_v across all fields that work appears in).
- **work_v**: `(source_v + mean_inst_v) / 2` — equal 50/50 weight between source and institution pool.
  `mean_inst_v` = unweighted mean v of affiliated institutions in the field's ranking.
  Null mean_inst_v (no retained institutions) filled with source_v.
- **HCW with work_v < 1**: ~15% of all HCW (~49,858 / 341,695 total HCW).

### Counts (window 2020_2024, label=baseline)
341,695 total HCW across 26 fields; ~49,858 have work_v < 1 (~14.6% overall).
Fraction varies by field: Pharmacology 34.9%, Vet 37.8%, Nursing 29.8% at high end;
Arts&Hum 7.5%, SocSci 7.9%, Ag&Bio 10.5% at low end.

### Pipeline stages
- **E1** `build_enclave_hcw.py`: single-pass over flat_works + corpus_references using
  DuckDB TEMP TABLEs; attaches source_v and mean_inst_v from field rankings; adds titles
  from OA works parquet. Runtime ~218s. Output: `enclave_hcw_{window}_{label}.parquet`.
- **E2** `tfidf_enclave.py`: TF-IDF lift analysis comparing HCW with work_v<1 vs rest.
  Output: `enclave_tfidf_{window}_{label}.parquet`.
- **E3** `nmf_enclave.py`: NMF topic clustering on HCW titles per field.
  Adaptive k = min(10, n_enc // 20); min 30 works to run.
  Output: `enclave_nmf_{window}_{label}.parquet`.

### Connectivity check (Engineering field 22, Maths field 26)
HCW with work_v < 1 are NOT tightly connected citation rings:
- Engineering: 2,922 HCW, 259K distinct citers, 0.5% loop rate, 11% internal citation rate
- Maths: 1,016 HCW, 40K distinct citers, 1.4% loop rate, 25% internal citation rate
These are large diffuse sub-fields, not orchestrated citation rings (unlike EconBusiness
where the corpus was more curated and rings were visible against a tighter background).

## TODO — next: spectral_analysis/
- Compute top-k eigenvectors for small-gap fields (Arts 0.117, Business 0.091, Econ 0.149, Vet 0.110, Engineering 0.210, Maths 0.332)
- Embed sources+institutions in eigenvector space → identify co-clusters / subfield structure
- The second eigenvector sign-pattern partitions the field into its two dominant sub-communities

## TODO — Australian HEP heatmap
- `analysis/hep_heatmap.py` produces the heatmap for 26 OA fields with baseline rankings
- Extension: Leiden-level heatmap (5 columns instead of 26)
- Extension: bloc-filtered heatmap (OECDG20CIA, CIAA) once bloc runs complete

## TODO — Subfield rankings
- Investigate running spectral ranking at the OA subfield level (below the 26 fields)
  to expose finer disciplinary structure; assess corpus sparsity vs. spectral signal trade-off

## Parameter flags (all implemented)

### ε — epsilon (cross-boundary sentinel, default 0)
- ε=0: standard edge list (only within-field retained↔retained citations)
- ε=1: append sentinel edges (`source_idx=1`, `institution_idx=1`) that absorb
  cross-field citations; sentinel v-scores masked to NaN in output.

**Semantics**: cross-field = any corpus_reference pair where citer xor cited is
NOT in the retained `_fw` set for this field.
- type1 (retained citer → sentinel cited): citer retained, cited not retained in this field
- type2 (sentinel citer → retained cited): citer not retained in this field, cited retained
- `edge_field_weight` = `field_weight / 2` (half of the retained side's weight, zero for the other)
- type1 `R_i` = within-field `R_i` of citer (reuses `_Ri`); type2 `R_i` = `SUM(cited_fw/2)` for that citer

**Status**: ✅ implemented
- `build_edge_list_field.py`: `SX_IDX/IX_IDX = 1`, `_add_epsilon_edges()` inserts type1+type2 rows
  into `_edges_out` before the final COPY when `run.epsilon == 1`.
- `build_csr_field.py`: injects sentinel rows into `src_df`/`inst_df` (not in candidacy tables),
  sets `is_sentinel_s`/`is_sentinel_u` bool masks on the returned `CSRData`.
- `katz_ranker.py`: already masks sentinel positions to NaN in `v_s`/`v_u` via `_mask_nan()`.

### ω — omega (institution weighting mode, default 0)
- ω=0: **author-fractional** weighting — institution credit proportional to author share
  (`inst_weight / cited_inst_weight` columns from edge list)
- ω=1: **direct 1/N_inst** weighting — equal credit per affiliated institution
  (`direct_inst_weight / direct_cited_inst_weight` columns from edge list)

**Status**: ✅ implemented — `build_csr_field.py` selects `direct_inst_weight` /
`direct_cited_inst_weight` when `run.omega == 1`, aliased as `inst_weight` /
`cited_inst_weight` in `_el` so block queries are unchanged.
Both columns have been in the edge list (and `flat_works`) since the original build.

### β — beta (unit self-reference exclusion, default 0)
- β=0: self-references included (baseline)
- β=1: exclude edges where `citer_source_idx == cited_source_idx`
  AND `citer_inst_idx == cited_inst_idx`

**Status**: ✅ implemented — `build_csr_field.py` appends
`WHERE NOT (citer_source_idx = cited_source_idx AND citer_inst_idx = cited_inst_idx)`
to the `_el` materialisation when `run.beta == 1`.
The edge-list file itself is unchanged; the filter is applied at CSR-build time.

### 🌐 bloc — country-group filter (default `''` = all countries)
Restricts the works corpus to works affiliated with institutions in a named country bloc.
Bloc → country code mapping is read from `data/bloc.xlsx` into `GlobalSettings.blocs`.

- `bloc=''`: no filter; all countries included (current behavior)
- `bloc='AU'`: Australia only
- `bloc='CIAA'`: China, India, America, Australia (`{AU, CN, IN, US}`)
- `bloc='OECDG20'`: union of OECD (38) and G20 (19 sovereign members), 46 countries total
- `bloc='OECDG20-CIA'`: OECDG20 minus `{CN, IN, US}` (43 countries)

**Semantics**: all-in — a work is included only if EVERY affiliated institution (in that
field+window) has a country_code in the bloc.  A single out-of-bloc collaborator excludes
the whole work.  Below-tau institutions still count for this test (they are checked in
the full flat_works scan before the tau filter is applied).

**Status**: ✅ implemented
- `build_flat_works.py` — carries `country_code VARCHAR` from `institutions.country_code`
  through `_valid_inst` → `_iw` → parquet output.
- `build_edge_list_field.py` — `build_edge_list(..., bloc_codes=())`: when non-empty,
  materialises `_excl_works` (works with any out-of-bloc institution) then applies
  `work_idx NOT IN (SELECT work_idx FROM _excl_works)` before the tau filter.
- `run_leiden_bloc.py` — resolves `settings.blocs[run.bloc]` and passes to `build_edge_list`.
- Candidacy (`build_field_candidacy.py`) is NOT bloc-filtered; candidacy thresholds are
  global.  Bloc runs share the same candidacy parquets as the baseline run.
