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
    build_work_v.py            #   ✅ stage 4c: work_v = (v_S + mean(v_I))/2 for all retained works
    run_leiden_sensitivity.py  #   ✅ parameter sensitivity suite for Leiden
    run_leiden_bloc.py         #   ✅ country-bloc Leiden rankings
    run_field_bloc.py          #   ✅ country-bloc OA field rankings
    pipeline_status.py         #   ✅ "where am I up to" freshness report — see Pipeline guard system
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
    build_enclave_hcw.py       #   ✅ HCW detection + v attachment (stage E1)
    tfidf_enclave.py           #   ✅ TF-IDF term lift per field (stage E2)
    nmf_enclave.py             #   ✅ NMF topic clustering per field (stage E3)
    network_enclave.py         #   ✅ 1-hop citation network per enclave (stage E4)
    researcher_enclaves.py     #   ✅ top-3 frequent authors per enclave network (stage E5)
    reports/                   #   per-field MD reports (network + researcher)
  spectral_analysis/           # next: eigenvector community structure
  util/
    load_config.py             #   load_config() → Paths, load_runs() → list
    runs.py                    #   Run dataclass + GlobalSettings + LEIDEN_GROUPS
    guard.py                   #   freshness tracking for pipeline outputs — see Pipeline guard system
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
  work_topics/*.parquet      OA snapshot — work×topic×score + full hierarchy
  sources.parquet            OA snapshot — source metadata
  institutions.parquet       OA snapshot — institution metadata
  references/*.parquet       OA snapshot — (citer_idx, cited_idx)

WORKING/
  flat_works_2000_2025.parquet              ✅ stage 1a; master table — 261.4M rows, 52.8M
                                              distinct works (rebuilt 2026-07-04 with the
                                              title-dedup/authors_count<=29/work_types recode).
                                              (work×institution×subfield, with weights)
                                              cols: work_idx, publication_year,
                                                source_idx, institution_idx, country_code,
                                                inst_weight, direct_inst_weight,
                                                subfield_idx, subfield_name,
                                                field_idx, field_weight,
                                                leiden_idx, leiden_name,
                                                referenced_works_count,
                                                title, cited_by_count, authors_count,
                                                institutions_distinct_count
                                                (last 4 cols: raw OA values, unrecomputed)
  corpus_references_2000_2025.parquet       ✅ stage 1b; 1.12B rows (rebuilt alongside
                                              flat_works, 2026-07-04)
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
  work_v_{window}_{label}.parquet          ✅ stage 4c; work_v for all retained works
                                             cols: work_idx, field_idx, source_v,
                                               mean_inst_v (null if no retained insts),
                                               work_v = (source_v + COALESCE(mean_inst_v,source_v))/2
                                             66,086,311 rows across 26 OA fields
  filtered_works_topics.parquet            ✅ hca_extract.py stage 1; 71.4M rows, 24.4M works
  hcw_flat_works.parquet                   ✅ hca_extract.py stage 2; 44.8M rows, 24.4M works
  hcw_works.parquet                        ✅ hca_extract.py stage 3; 455,129 rows (HCW, top 1%)
  hcw_authors.parquet                      ✅ hca_extract.py stage 4; 1,372,362 rows, 697,008 authors
  hcw_authorships.parquet                  ✅ hca_extract.py stage 5; 1,786,820 rows
  hca_crossfield_{window}_{label}.parquet  ✅ 697,013 authors, cross-field paper+cite scores
```
Stage E4 + E5 outputs are MD files in `enclaves/reports/`:
```
enclaves/reports/
  {fid}_{window}_{label}.md              ✅ stage E4; 1-hop network report per field
  {fid}_{window}_{label}_researchers.md  ✅ stage E5; top-3 authors per enclave per field
```
`window` = `{census_start}_{census_end}` (e.g. `2020_2024`).
`field_idx` = OA field 11–36, Leiden group 1–5, or subfield ≥ 1000.

## Pipeline status — window 2020_2024
All stages ✅ complete and verified fresh as of 2026-07-04 (full rebuild against the
flat_works recode — see "flat_works master table recode" below). Run
`.venv/bin/python pipeline/pipeline_status.py` for a live freshness report — every
guarded output was 421/421 fresh at last check. See "Pipeline guard system" below for
how staleness is now caught automatically instead of silently.

- ✅ Stage 1a/1b: `build_flat_works.py` — flat_works/corpus_references, recoded schema
  (title, cited_by_count, authors_count, institutions_distinct_count carried verbatim;
  title dedup + authors_count<=29 filter; work_types broadened to 8 types)
- ✅ Stage 2: `build_field_candidacy.py` — all 6 candidacy parquets
- ✅ Stage 3+4: `run_field_bloc.py` / `run_leiden_bloc.py` — baseline + all 4 blocs
  (OECDG20, OECDG20CIA, CIAA, BASELINECIA), 26 OA fields + 5 Leiden groups each
- ✅ Sensitivity suite: `run_leiden_sensitivity.py` — 5 variants × 5 Leiden groups
- ✅ Stage 4c: `build_work_v.py` — work_v_2020_2024_baseline.parquet
- ✅ Stage E1–E3: HCW detection, TF-IDF, NMF — all 26 fields. NMF topic names (the
  26-field AI merge+name pass) will need reapplying after any future from-scratch
  rebuild, since `enclave_nmf_*.parquet` regenerates without them.
- ✅ Stage E4: `network_enclave.py` — enclave network MD reports (26 fields)
  - Summary cols: n_hcw--, ⟨v⟩--, ⟨v⟩net, n_hcw-+/%-+, n_hcw+-/%+-, n_hcw++/%++,
                  citers, loop%, n_comp, lg_hcw, lg%
  - ⟨v⟩net covers ALL 1-hop network works (seeds + citers) with retained source
- ✅ Stage E5: `researcher_enclaves.py` — 26 researcher MD reports. The `a.id`/
  `summary_stats.h_index` schema bug (current OA authors parquet has flat `author_idx`/
  `h_index` columns, not a URL-style `id` or nested `summary_stats`) is fixed.
  - Top-3 authors per enclave by distinct works in the 1-hop network
  - Fields: display_name, h_index, works_count, cited_by_count, in_network count
- ✅ `enclaves/plot_hca_hcr.py` — 6 PDFs (combined + 5 Leiden group), HCR-overlay included

## flat_works master table recode — ✅ DONE, rebuilt 2026-07-04
`flat_works` now carries `title`, `cited_by_count`, `authors_count`,
`institutions_distinct_count` verbatim from raw OA works (not recomputed after
institution/topic pruning), plus two new filters: `title IS NOT NULL` + dedup to the
earliest (lowest publication_year, then work_idx) work per duplicate title, and
`authors_count <= MAX_AUTHORS` (29, in `pipeline/build_flat_works.py`) to drop
hyper-authored consortium papers. Goal: `flat_works` is the single master table other
stages should read from instead of re-scanning raw OA works.

**Reconciled**: `settings.yaml`'s `work_types` broadened from `[article, review]` to the
full 8-type list (`article, review, book, book-chapter, dissertation, letter, preprint,
report`), matching `hca_extract.py`'s own list — affects the core ranking corpus
project-wide, not just HCA matching (expected impact: small, per explicit sign-off).
`analysis/hca_extract.py` stage 1 (`build_filtered_works_topics`) now reads its base
work list from `flat_works` instead of independently re-scanning raw OA works — title
dedup, type filter, is_paratext/is_retracted, and `authors_count<=29` are all inherited
from flat_works; stage 1 now only adds its own `cited_by_count > (2027-year)` filter.
The now-redundant `authors_count <= MAX_AUTHORS` check in stage 2
(`build_hcw_flat_works`) was removed; the `MAX_AUTHORS` constant was removed from
`hca_extract.py` entirely (now only in `build_flat_works.py`, value 29).

**Side effect of this consolidation** (not explicitly requested, flagged for awareness):
the HCA candidate pool now also inherits flat_works' other membership rules it never had
before — `referenced_works_count > 0`, source type in the retained set (journal/
conference/book series), and ≥1 qualifying-institution-type authorship. A highly-cited
work failing any of those (e.g. zero references, hosted on a non-retained source type,
or authored only by company/funder/archive-affiliated people) no longer enters the HCA
candidate pool, whereas it would have before. Expected to be rare among genuinely
highly-cited works.

**Why flat_works row count grew ~70% (153.5M → 261.4M rows) despite the new filters
being restrictive**: NOT primarily the recode above. The dominant cause was a separate,
same-day fix (commit `bd8041c`, landed *after* the row-count-153.5M build had already
run) that changed the topics join from a legacy `topics/*.parquet` (already reduced to
one row per work — its single *main* topic) to `work_topics/*.parquet` (one row per work
with a `topics` array) unnested against `topics.parquet` (the topic→subfield/field
definition table — the rename was cosmetic, just to avoid the name clash with this
definition table). The material change: flat_works now gets a row per (work ×
institution × every assigned topic's subfield) instead of just the main topic's
subfield, since most OA works carry several topics. Measured contribution of the other
two changes: work_types broadening (2→8 types) added only +5.5% of final distinct works;
the title-dedup + `authors_count<=29` filter reduced the raw eligible pool by ~0.35%
— both small, as CLAUDE.md's original "expected impact: small" note anticipated for
work_types. **Lesson**: when judging whether a WORKING/ output reflects current code,
compare its mtime against `git log -- <script>` for *every* commit touching that script,
not just the most recent one you're aware of — a same-day commit can still postdate a
same-day build.

**Done**: `enclaves/build_enclave_hcw.py`'s title-attach step now reads `title` from
`flat_works` instead of a separate OA works scan (simplification only, no behavior change
beyond inheriting flat_works' new title-dedup).

**Also fixed** (prerequisite, in `analysis/hca_extract.py` and `analysis/hca_crossfield.py`):
HCA qualification (`n_hcw`, `cf_paper_score`, `cf_cite_score`) now counts only HCW with
`publication_year` in `[HCA_YEAR_MIN, HCA_YEAR_MAX] = [2014, 2024]`, matching Clarivate's
rolling ~11-year HCR citation window — HCW *selection* itself (stage 1–3, which works can
be a HCW at all) is unaffected and still spans the full 2000–2025 range.

**Test coverage**: `pipeline/tests/test_build_flat_works.py`'s synthetic fixture was extended
with a duplicate-title pair and a 35-author work to cover the two new filters, plus a
raw-passthrough test for `title`/`cited_by_count`/`authors_count`/`institutions_distinct_count`.
Full project test suite (389 tests, `util`/`pipeline`/`analysis`/`enclaves`, including
`util/tests/test_guard.py`) passes clean — run with `.venv/bin/python -m pytest util/tests
pipeline/tests analysis/tests analysis/test_hcr_inst_oax.py enclaves/tests --import-mode=importlib`.

### Rerun order — now automatic (see Pipeline guard system below)
Every stage listed above is guard-wired: each script checks its own inputs' and its own
(plus its real code dependencies') mtimes against a `<output>.guard.json` sidecar before
deciding to skip a rebuild, so manual delete-then-rerun is no longer required to pick up
a code or data change. To rebuild everything from scratch in dependency order, just run
in sequence (each auto-skips whatever is already fresh):
```
.venv/bin/python pipeline/build_flat_works.py
.venv/bin/python pipeline/build_field_candidacy.py
.venv/bin/python pipeline/run_field_bloc.py
.venv/bin/python pipeline/run_leiden_bloc.py
.venv/bin/python pipeline/run_leiden_sensitivity.py
.venv/bin/python pipeline/build_work_v.py
.venv/bin/python analysis/hca_extract.py
.venv/bin/python analysis/hca_crossfield.py
.venv/bin/python enclaves/build_enclave_hcw.py
.venv/bin/python enclaves/tfidf_enclave.py
.venv/bin/python enclaves/nmf_enclave.py
.venv/bin/python enclaves/network_enclave.py
.venv/bin/python enclaves/researcher_enclaves.py
.venv/bin/python enclaves/plot_hca_hcr.py
```
Append `--yes`/`-y` to any of them to skip the confirmation prompt on an expensive stale
rebuild (safe for unattended/background runs). Run `.venv/bin/python
pipeline/pipeline_status.py` any time to see what's fresh/stale without rebuilding anything.

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

## Bloc runs (Leiden + OA field, window 2020_2024)
All-in filter: a work is included only if EVERY affiliated institution is in the bloc.
Candidacy parquets are global (shared with baseline); new edge lists built per bloc.
Applies to both `run_field_bloc.py` (26 OA fields) and `run_leiden_bloc.py` (5 Leiden groups).

| label       | bloc key     | countries |
|-------------|--------------|-----------|
| OECDG20     | OECDG20      | 46 (OECD ∪ G20) |
| OECDG20CIA  | OECDG20-CIA  | 43        |
| CIAA        | CIAA         | 4 (AU,CN,IN,US) |
| BASELINECIA | BASELINE-CIA | all OA countries except CN,IN,US |

## Analysis outputs
- `analysis/leiden_facets.pdf` — 2×5 log(v) vs rank; multi-label overlay, default all 6 labels
- `analysis/leiden_{label}.pdf` — 7 PDFs from `leiden_bloc_facets.py`, one per comparison:
  - `leiden_OECDG20CIA.pdf`, `leiden_CIAA.pdf` — bloc vs baseline (coloured scatter)
  - `leiden_tau20.pdf`, `leiden_rho1.pdf`, `leiden_eps1.pdf`, `leiden_om1.pdf`, `leiden_beta1.pdf` — variants vs baseline (green scatter)
- `analysis/hep_heatmap.pdf` — AU HEP institution scores across 26 OA fields

## Pipeline guard system
Every skip-if-exists cache checkpoint in the pipeline used to trust bare file existence,
which let a stage silently skip rebuilding even when its inputs (or its own code) had
changed since the output was last built — happened twice in one session on 2026-07-04
(155 baseline/bloc rankings, and one `tau20` leiden-1 ranking, sat stale under freshly
rebuilt edge lists with no visible symptom).

`util/guard.py` fixes this: every guarded output gets a `<output>.guard.json` sidecar
recording the exact (path, mtime, size) of each declared input and the mtime of each
declared producing script at build time. `guard.ensure_fresh(out_path, *inputs,
script=__file__, auto_yes=args.yes, label=...)` replaces the bare existence check —
returns `True` if the caller should (re)build, `False` if fresh. If stale and the
previously recorded build took longer than ~3 minutes (or there's no timing history),
it prompts for confirmation on stdin unless `auto_yes=True` (wired to a `--yes`/`-y`
flag on every guarded script). Script mtime is tracked as a real dependency, not just
data files — this is what would have caught the same-day `build_csr_field.py` ρ=0 NaN
fix (see katz_ranker.py history) automatically invalidating rankings built with the
buggy code, without anyone having to remember to delete them.

Wired into: `build_flat_works.py`, `build_field_candidacy.py`, `run_field_bloc.py`,
`run_leiden_bloc.py`, `run_leiden_sensitivity.py`, `build_work_v.py`, `hca_extract.py`,
`hca_crossfield.py`, `build_enclave_hcw.py`, `tfidf_enclave.py`, `nmf_enclave.py`,
`network_enclave.py`, `researcher_enclaves.py`, `plot_hca_hcr.py` — i.e. every stage.

`pipeline/pipeline_status.py` answers "where am I up to" in one command: it `rglob`s the
whole project tree (guard sidecars live next to whatever they guard — e.g. E4/E5 report
guards are in `enclaves/reports/`, plot guards in `enclaves/plots/`, not just WORKING/)
for `*.guard.json` files and re-checks each one's freshness right now. It also flags
WORKING/ outputs matching known pipeline naming patterns with no guard record at all
("unmanaged" — predates the system or was built by a script not yet wired up).

Pattern for adding a new guarded stage:
```python
if guard.ensure_fresh(out_path, *input_paths, script=__file__,
                      auto_yes=args.yes, label='thing being built'):
    t0 = time.time()
    ... build ...
    guard.record_build(out_path, *input_paths, script=__file__,
                       build_seconds=time.time() - t0)
```
When a build has multiple real script dependencies (e.g. rankings depend on
`run_rankings.py` *and* `build_csr_field.py` *and* `katz_ranker.py`), pass `script=` a
**list** and reuse the exact same list in both the `ensure_fresh` and `record_build`
calls — passing a shorter list to `record_build` silently drops those scripts from
future freshness checks. Tests: `util/tests/test_guard.py` (16 tests).

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
- **ARPACK/NaN crash on ε=1 sensitivity edge lists**: `build_csr_field.py`'s ρ=0 weighting
  computed `ew = edge_field_weight * (r_bar / R_i)`. One ε=1 sentinel-citer edge had
  `R_i = edge_field_weight = 0.0`, so `0.0 * (r_bar / 0.0) = 0.0 * inf = NaN`. That single
  NaN poisoned the whole `SUM(...) GROUP BY citer_source_idx` aggregate for the sentinel
  row, crashing ARPACK's eigensolve (`DLASCL`/Arnoldi errors) on leiden 2's `eps1` ranking.
  Confirmed absent from all 26 baseline (non-ε) edge lists — only the ε=1 sentinel path is
  affected. Fixed with `CASE WHEN R_i = 0 THEN 0.0 ELSE r_bar/R_i END`.
- **`researcher_enclaves.py` stale OA authors schema**: queried `a.id` (regex-parsed to get
  author_idx) and `a.summary_stats.h_index`, but the current OA authors parquet snapshot
  has `author_idx` and `h_index` as flat top-level columns already (no `id` VARCHAR, no
  nested `summary_stats` struct — only a struct `ids` with `openalex`/`orcid` sub-fields).
  This silently broke stage E5 entirely. Fixed to query `a.author_idx`/`a.h_index` directly.
- **`util/tests/test_runs.py` / `test_load_runs.py` silently stale**: `Run` had been refactored
  (`window` became a derived `@property` from `tc0`/`tc1` instead of a constructor kwarg, `m`
  changed from a `'0110'` string to a `(0,1,1,0)` tuple, the `run_code` field was dropped,
  `el_path()`'s filename format changed from tau-embedded to label-based) but the tests were
  never updated — 18 failures, unrelated to any single session's changes, just drift over time.
  Fixed by rewriting both test files against current `Run`/`load_runs()` behavior; also moved
  `test_load_runs.py` off the live, evolving `params.csv` onto local CSV fixtures (only
  `test_skip_filters` did this before) so the suite won't silently drift again as the experiment
  list changes. Run `.venv/bin/python -m pytest <dir> --import-mode=importlib` — plain `pytest`
  errors on collection because `pipeline/tests/` has no `__init__.py` while the other three
  `tests/` packages do, causing a module-name collision across separately-run test directories.

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

## Field index mapping (OA → Clarivate HCR categories)
Clarivate HCR uses ESI categories, not OA fields. Several OA fields map to the same Clarivate
category; fields with no dedicated category are subsumed under a broader one.
Source: `analysis/hca_report.py` (CONCORDANCE list, now archived).

| field_idx | OA Field                                      | Clarivate HCR Category                       | Note |
|-----------|-----------------------------------------------|----------------------------------------------|------|
| 11        | Agricultural and Biological Sciences          | Agricultural Sciences + Plant and Animal Science | Ag+plant/animal covers ag+bio scope; misses pure cell biology |
| 12        | Arts and Humanities                           | Social Sciences                              | No dedicated Arts & Humanities category; Social Sciences is closest |
| 13        | Biochemistry, Genetics and Molecular Biology  | Biology and Biochemistry + Molecular Biology and Genetics | Both categories together span biochem/genetics/molbio |
| 14        | Business, Management and Accounting           | Economics and Business                       | Same Clarivate category used for fields 18, 20 |
| 15        | Chemical Engineering                          | Engineering                                  | Shared with fields 21, 22; chemical engineering subsumed in Engineering |
| 16        | Chemistry                                     | Chemistry                                    | Good match |
| 17        | Computer Science                              | Computer Science                             | Good match |
| 18        | Decision Sciences                             | Economics and Business                       | Shared with fields 14, 20; Decision Sciences subsumed |
| 19        | Earth and Planetary Sciences                  | Geosciences                                  | Good match; Space Science excluded (planetary astro minor) |
| 20        | Economics, Econometrics and Finance           | Economics and Business                       | Shared with fields 14, 18 |
| 21        | Energy                                        | Engineering                                  | Shared with fields 15, 22; energy subsumed in Engineering |
| 22        | Engineering                                   | Engineering                                  | Good match |
| 23        | Environmental Science                         | Environment and Ecology                      | Good match |
| 24        | Immunology and Microbiology                   | Immunology + Microbiology                    | Two Clarivate categories combine cleanly |
| 25        | Materials Science                             | Materials Science                            | Good match |
| 26        | Mathematics                                   | Mathematics                                  | Good match |
| 27        | Medicine                                      | Clinical Medicine                            | Good match; shared with fields 29, 35, 36 |
| 28        | Neuroscience                                  | Neuroscience and Behavior                    | Good match |
| 29        | Nursing                                       | Clinical Medicine                            | Shared with field 27; Nursing subsumed |
| 30        | Pharmacology, Toxicology and Pharmaceutics    | Pharmacology and Toxicology                  | Good match |
| 31        | Physics and Astronomy                         | Physics + Space Science                      | Physics+Space Science spans field 31 well |
| 32        | Psychology                                    | Psychiatry and Psychology                    | Partial — Clarivate combines psychiatry with psychology |
| 33        | Social Sciences                               | Social Sciences                              | Shared with field 12 |
| 34        | Veterinary                                    | Plant and Animal Science                     | Partial — animal science overlaps; no dedicated veterinary category |
| 35        | Dentistry                                     | Clinical Medicine                            | Shared with field 27; Dentistry subsumed |
| 36        | Health Professions                            | Clinical Medicine                            | Shared with field 27; Health Professions subsumed |

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
  Year range: 2014–2023 (10-year window). Rankings window: 2020_2024.
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
