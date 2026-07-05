# CHANGELOG — SpectralRankingGlobal

Historical record of major recodes, incidents, and bug fixes. For current pipeline
status and how to run things, see `CLAUDE.md`. For folder structure, schemas, and
design/parameter reference, see `REFERENCE.md`.

## 2026-07-04 — flat_works master table recode + full pipeline rebuild

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
— both small, as originally anticipated for work_types. **Lesson**: when judging whether
a WORKING/ output reflects current code, compare its mtime against `git log -- <script>`
for *every* commit touching that script, not just the most recent one you're aware of —
a same-day commit can still postdate a same-day build.

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
Full project test suite (389 tests) passes clean.

After the recode landed, the full pipeline (candidacy → edge lists → rankings → work_v →
HCA extract/crossfield → enclave E1–E5 → plots) was deleted and rebuilt from scratch
against it, and verified fresh via `pipeline_status.py` (421/421 guarded outputs).

## 2026-07-04 — Pipeline guard system introduced

Every skip-if-exists cache checkpoint in the pipeline used to trust bare file existence,
which let a stage silently skip rebuilding even when its inputs (or its own code) had
changed since the output was last built. This happened twice in the same session: 155
baseline/bloc rankings, and one `tau20` leiden-1 ranking, sat stale under freshly rebuilt
edge lists with no visible symptom — the rankings still existed from before the
flat_works recode, so `run_field_bloc.py`/`run_leiden_bloc.py`/`run_leiden_sensitivity.py`
skipped reranking even though the edge list underneath had completely changed.

Fix: `util/guard.py` — see the "Pipeline guard system" section in `REFERENCE.md` for the
current design and usage pattern. Wired into every build stage in the pipeline the same
day; `util/tests/test_guard.py` (16 tests) covers it.

## 2026-07-04 — ARPACK/NaN crash on ε=1 sensitivity edge lists

`build_csr_field.py`'s ρ=0 weighting computed `ew = edge_field_weight * (r_bar / R_i)`.
One ε=1 sentinel-citer edge had `R_i = edge_field_weight = 0.0`, so
`0.0 * (r_bar / 0.0) = 0.0 * inf = NaN`. That single NaN poisoned the whole
`SUM(...) GROUP BY citer_source_idx` aggregate for the sentinel row, crashing ARPACK's
eigensolve (`DLASCL`/Arnoldi errors) on leiden 2's `eps1` ranking. Confirmed absent from
all 26 baseline (non-ε) edge lists — only the ε=1 sentinel path is affected.
Fixed with `CASE WHEN R_i = 0 THEN 0.0 ELSE r_bar/R_i END`.

## 2026-07-04 — researcher_enclaves.py stale OA authors schema

`enclaves/researcher_enclaves.py` queried `a.id` (regex-parsed to get author_idx) and
`a.summary_stats.h_index`, but the current OA authors parquet snapshot has `author_idx`
and `h_index` as flat top-level columns already (no `id` VARCHAR, no nested
`summary_stats` struct — only a struct `ids` with `openalex`/`orcid` sub-fields). This
silently broke stage E5 entirely (had been stale since Jun 27). Fixed to query
`a.author_idx`/`a.h_index` directly; verified against real data after the fix.

## HCW enclave connectivity check (Engineering field 22, Maths field 26)

HCW with work_v < 1 are NOT tightly connected citation rings:
- Engineering: 2,922 HCW, 259K distinct citers, 0.5% loop rate, 11% internal citation rate
- Maths: 1,016 HCW, 40K distinct citers, 1.4% loop rate, 25% internal citation rate

These are large diffuse sub-fields, not orchestrated citation rings (unlike EconBusiness
where the corpus was more curated and rings were visible against a tighter background).

## Earlier fixes

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
