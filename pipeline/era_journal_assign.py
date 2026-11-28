"""
era_journal_assign.py — ERA 2018 journal-list assignment of works to FoR 2008 codes.

The ERA emulation's default method maps each OpenAlex subfield to exactly one FoR 2008
group (research_classification.Resolver, "crosswalk"). ERA 2018 itself assigned journal
articles through the ERA 2018 Journal List, where each journal carries up to three FoR codes
(2- or 4-digit) or MD (multidisciplinary). This module emulates that:

  journal works  a work in a source matched to a non-MD ERA journal gets share 1/k on each of
                 the journal's k codes.
                   division level: each code's division receives its share.
                   group level:    a 4-digit code receives its share; a 2-digit code's share is
                                   spread over the work's crosswalk groups inside that division
                                   (in proportion to their subfield weights), or dropped at
                                   group level when the work has none there.
  other works    works in MD journals, unlisted journals and conference sources keep the
                 crosswalk weights (subfield field_weight summed by group / division).

ERA journal → OpenAlex source matching: ISSN (any of the 7 ERA ISSNs against OpenAlex issn
and issn_l), then exact title for unmatched journals (same rule as the MD whitelist builder).

Outputs (one set per level, in <era_dir>/candidacy/):
  fw_journal_{level}_2011_2016.parquet   flat_works-shaped table, one row per
                                         (work, institution, code): subfield_idx = int(code),
                                         field_weight = share of the work in that code
  source/inst candidacy for the same codes (field_idx = int(code))
  era_journal_sources.parquet            source_idx → codes (audit)
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import openpyxl
import pandas as pd


def norm_code(x) -> str | None:
    """ERA list FoR cell → '01'..'22' (division), '0101'..'2299' (group), 'MD', or None."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    if not s:
        return None
    if s.upper() == 'MD':
        return 'MD'
    try:
        v = int(float(s))
    except ValueError:
        return None
    return f'{v:02d}' if v < 100 else f'{v:04d}'


def load_era_journals(xlsx: Path) -> pd.DataFrame:
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    rows = []
    for r in wb['ERA 2018 Journal List'].iter_rows(min_row=2, values_only=True):
        era_id, title = r[0], r[1]
        codes = [c for c in (norm_code(r[3]), norm_code(r[5]), norm_code(r[7])) if c]
        issns = [str(x).strip().replace('-', '').upper() for x in r[9:16] if x and str(x).strip()]
        rows.append(dict(era_id=era_id, title=str(title or '').strip(), codes=codes, issns=issns))
    wb.close()
    return pd.DataFrame(rows)


def match_sources(db: duckdb.DuckDBPyConnection, era: pd.DataFrame, sources_pq: str) -> pd.DataFrame:
    """(source_idx, era_id, match_method), one ERA journal per source (ISSN before title)."""
    issn = era[['era_id', 'issns']].explode('issns').dropna().rename(columns={'issns': 'issn'})
    db.register('_era_issn', issn)
    db.register('_era', era[['era_id', 'title']])
    return db.execute(f"""
        WITH src AS (
            SELECT source_idx, display_name, REPLACE(UPPER(i), '-', '') AS issn
            FROM (SELECT source_idx, display_name, UNNEST(issn) AS i FROM read_parquet('{sources_pq}')
                  WHERE issn IS NOT NULL
                  UNION SELECT source_idx, display_name, issn_l FROM read_parquet('{sources_pq}')
                  WHERE issn_l IS NOT NULL)
        ),
        by_issn AS (
            SELECT DISTINCT s.source_idx, e.era_id, 'issn' AS match_method
            FROM _era_issn e JOIN src s USING (issn)
        ),
        by_title AS (
            SELECT DISTINCT s.source_idx, e.era_id, 'title' AS match_method
            FROM _era e JOIN read_parquet('{sources_pq}') s
              ON lower(trim(e.title)) = lower(trim(s.display_name))
            WHERE e.era_id NOT IN (SELECT era_id FROM by_issn)
        ),
        allm AS (SELECT * FROM by_issn UNION ALL SELECT * FROM by_title)
        SELECT source_idx::BIGINT AS source_idx, era_id, match_method
        FROM allm
        QUALIFY row_number() OVER (PARTITION BY source_idx
                                   ORDER BY (match_method = 'issn') DESC, era_id) = 1
    """).fetchdf()


def build_journal_tables(db: duckdb.DuckDBPyConnection, fw_path: str, era_xlsx: Path,
                         sources_pq: str, grp_of_subfield: dict[int, str],
                         out_dir: Path, y0: int, y1: int, window: str) -> dict[str, str]:
    """Write the journal-method flat tables and candidacy; return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    era = load_era_journals(era_xlsx)
    m = match_sources(db, era, sources_pq)
    codes = era[['era_id', 'codes']].explode('codes').dropna().rename(columns={'codes': 'code'})
    codes = codes[codes.code != 'MD']
    k = codes.groupby('era_id').code.transform('size')
    codes = codes.assign(share=1.0 / k)
    src_codes = m.merge(codes, on='era_id')[['source_idx', 'era_id', 'code', 'share']]
    src_codes.to_parquet(out_dir / 'era_journal_sources.parquet', index=False)
    db.register('_sc', src_codes)
    db.register('_xw', pd.DataFrame({'subfield_idx': list(grp_of_subfield),
                                     'grp': list(grp_of_subfield.values())}))

    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _w AS
        SELECT DISTINCT work_idx, source_idx, subfield_idx, field_weight
        FROM '{fw_path}' WHERE publication_year BETWEEN {y0} AND {y1}
    """)
    # crosswalk weight of each work in each group (all works)
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _wx AS
        SELECT w.work_idx, x.grp, SUM(w.field_weight) AS fw
        FROM _w w JOIN _xw x USING (subfield_idx)
        GROUP BY 1, 2
    """)
    db.execute("CREATE OR REPLACE TEMP TABLE _ws AS SELECT DISTINCT work_idx, source_idx FROM _w")
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _wj AS
        SELECT ws.work_idx, sc.code, sc.share
        FROM _ws ws JOIN _sc sc USING (source_idx)
    """)
    # group level
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _g AS
        -- 4-digit journal codes
        SELECT work_idx, code AS unit, share AS w FROM _wj WHERE length(code) = 4
        UNION ALL
        -- 2-digit journal codes spread over the work's crosswalk groups in that division
        SELECT work_idx, unit, w FROM (
            SELECT j.work_idx, x.grp AS unit,
                   j.share * x.fw / NULLIF(SUM(x.fw) OVER (PARTITION BY j.work_idx, j.code), 0) AS w
            FROM _wj j JOIN _wx x ON x.work_idx = j.work_idx AND left(x.grp, 2) = j.code
            WHERE length(j.code) = 2 AND x.fw > 0)
        WHERE w IS NOT NULL
        UNION ALL
        -- works without (non-MD) ERA journal codes: crosswalk
        SELECT x.work_idx, x.grp AS unit, x.fw AS w
        FROM _wx x WHERE x.work_idx NOT IN (SELECT work_idx FROM _wj)
    """)
    # division level
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _d AS
        SELECT work_idx, left(code, 2) AS unit, share AS w FROM _wj
        UNION ALL
        SELECT x.work_idx, left(x.grp, 2) AS unit, x.fw AS w
        FROM _wx x WHERE x.work_idx NOT IN (SELECT work_idx FROM _wj)
    """)
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _wi AS
        SELECT DISTINCT work_idx, publication_year, source_idx, institution_idx, country_code,
                        inst_weight, direct_inst_weight
        FROM '{fw_path}' WHERE publication_year BETWEEN {y0} AND {y1}
    """)
    paths = {}
    for level, tbl in (('group', '_g'), ('division', '_d')):
        fw_out = out_dir / f'fw_journal_{level}_{window}.parquet'
        db.execute(f"""
            COPY (
                SELECT wi.work_idx, wi.publication_year, wi.source_idx, wi.institution_idx,
                       wi.country_code, wi.inst_weight, wi.direct_inst_weight,
                       CAST(u.unit AS BIGINT) AS subfield_idx, u.unit AS subfield_name,
                       CAST(left(u.unit, 2) AS BIGINT) AS field_idx, u.w AS field_weight
                FROM (SELECT work_idx, unit, SUM(w) AS w FROM {tbl} GROUP BY 1, 2) u
                JOIN _wi wi USING (work_idx)
                WHERE u.w > 0 AND NOT isnan(u.w)
            ) TO '{fw_out}' (FORMAT PARQUET)
        """)
        sc_out = out_dir / f'journal_{level}_source_cands_{window}.parquet'
        ic_out = out_dir / f'journal_{level}_inst_cands_{window}.parquet'
        db.execute(f"""
            COPY (SELECT subfield_idx AS field_idx, source_idx, SUM(field_weight) AS weighted_works
                  FROM (SELECT DISTINCT work_idx, source_idx, subfield_idx, field_weight FROM '{fw_out}')
                  GROUP BY 1, 2) TO '{sc_out}' (FORMAT PARQUET)
        """)
        db.execute(f"""
            COPY (SELECT subfield_idx AS field_idx, institution_idx,
                         SUM(field_weight * inst_weight) AS weighted_works
                  FROM '{fw_out}' GROUP BY 1, 2) TO '{ic_out}' (FORMAT PARQUET)
        """)
        paths[level] = (str(fw_out), str(sc_out), str(ic_out))
    stats = db.execute("""
        SELECT (SELECT COUNT(DISTINCT work_idx) FROM _ws),
               (SELECT COUNT(DISTINCT work_idx) FROM _wj)
    """).fetchone()
    print(f'  journal method: {len(m):,} OpenAlex sources matched to ERA journals; '
          f'{stats[1]:,} of {stats[0]:,} works in the window carry ERA journal codes '
          f'({100 * stats[1] / max(stats[0], 1):.1f}%)', flush=True)
    return paths
