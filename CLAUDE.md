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

## Folder structure
```
SpectralRankingGlobal/
  pipeline/                  # all pipeline stages + ranking engine
    build_flat_works.py      #   ✅ stage 1: flat works table (work×institution×field)
    build_field_candidacy.py #   ✅ stage 2: per-field source/institution candidacy
    build_edge_list_field.py #   ✅ stage 3: field citation edge list
    build_csr_field.py       #   ☐ stage 4a: parquet edge list → CSRData
    run_rankings.py          #   ☐ stage 4b: orchestrate all fields
    katz_ranker.py           #   spectral algorithms (Perron/Katz, bipartite) — pure math
    summary_flat_works.py    #   diagnostic summary of flat_works
    tests/
  spectral_analysis/         # future
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
  flat_works_{ymin}_{ymax}.parquet              ✅ stage 1; ~97M rows
                                                  (work×institution×field, with weights)
  field_source_cands_{window}.parquet           ✅ stage 2; source weighted works per field
  field_inst_cands_{window}.parquet             ✅ stage 2; inst weighted works per field
  el_{field_idx}_{window}_tauS{s}_tauU{u}.parquet  stage 3; one per field
  rankings_{field_idx}_{window}.parquet         stage 4; one per field (or combined)
```
`window` = `{census_start}_{census_end}` (e.g. `2020_2024`).

## Pipeline status
- ✅ Stage 1: `build_flat_works.py` — flat_works_2016_2025.parquet produced (~97M rows)
- ✅ Stage 2: `build_field_candidacy.py` — candidacy parquets for 2020_2024
- ✅ Stage 3: `build_edge_list_field.py` — edge list construction (tested on Business)
- ☐ Stage 4a: `build_csr_field.py` — CSR matrix assembly
- ☐ Stage 4b: `run_rankings.py` — parameter driver
