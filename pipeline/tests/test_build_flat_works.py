"""
Tests for build_flat_works.py.

Uses small in-memory synthetic parquets written to tmp_path.
No connection to the real OA snapshot required.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import duckdb
from build_flat_works import build_flat_works


# ─── synthetic data ───────────────────────────────────────────────────────────

def make_test_parquets(tmp_path: Path) -> tuple[str, str, str, str, str]:
    """
    Write minimal synthetic parquet files to tmp_path.

    Works:
      101 — journal S1, 2020, article, valid
      102 — journal S1, 2020, review,  valid
      103 — conference S2, 2021, article, valid
      104 — repository S3, 2020, article → excluded: wrong source type
      105 — journal S1, 2015, article  → excluded: year out of range
      106 — journal S1, 2020, article, is_paratext=true  → excluded
      107 — journal S1, 2020, article, is_retracted=true → excluded
      108 — journal S1, 2020, book     → excluded: wrong work type
      109 — journal S1, 2020, article, valid but no topics → excluded

    Authorships (qualifying institutions only — all type='education' except inst 299):
      101: author 1001 → insts {201, 202};  author 1002 → inst {203}
           inst_weight: 201=0.25, 202=0.25, 203=0.50
           direct_inst_weight: 1/3 each
      102: author 1003 → inst {204}
           inst_weight: 204=1.0;  direct_inst_weight: 1.0
      103: author 1004 → inst {205};  author 1005 → inst {206}
           inst_weight: 205=0.5, 206=0.5;  direct_inst_weight: 0.5 each
      109: author 1006 → inst {207}   (valid, but no topics → excluded from output)

    Topics:
      101: topic 1 score 0.9 → field 11;  topic 2 score 0.6 → field 13
           field_weight: 11=0.6,  13=0.4
      102: topic 3 score 0.8 → field 11
           field_weight: 11=1.0
      103: topic 4 score 0.7 → field 17
           field_weight: 17=1.0
      (109 has no topics)

    Institutions:
      201, 202, 203, 204, 205, 206, 207 → type='education'  (valid)
      299 → type='company'  (excluded)
    """
    db = duckdb.connect()

    # sources
    db.execute("""
        CREATE TABLE sources AS FROM (VALUES
            ('https://openalex.org/S1', 'journal'),
            ('https://openalex.org/S2', 'conference'),
            ('https://openalex.org/S3', 'repository')
        ) t(id, type)
    """)
    src = str(tmp_path / 'sources.parquet')
    db.execute(f"COPY sources TO '{src}'")

    # institutions
    db.execute("""
        CREATE TABLE institutions AS FROM (VALUES
            ('https://openalex.org/I201', 'education'),
            ('https://openalex.org/I202', 'education'),
            ('https://openalex.org/I203', 'education'),
            ('https://openalex.org/I204', 'education'),
            ('https://openalex.org/I205', 'education'),
            ('https://openalex.org/I206', 'education'),
            ('https://openalex.org/I207', 'education'),
            ('https://openalex.org/I299', 'company')
        ) t(id, type)
    """)
    inst = str(tmp_path / 'institutions.parquet')
    db.execute(f"COPY institutions TO '{inst}'")

    # works
    db.execute("""
        CREATE TABLE works AS FROM (VALUES
            (101, 1, 2020, 10, 'article', false, false),
            (102, 1, 2020, 20, 'review',  false, false),
            (103, 2, 2021, 15, 'article', false, false),
            (104, 3, 2020,  5, 'article', false, false),
            (105, 1, 2015,  8, 'article', false, false),
            (106, 1, 2020, 12, 'article', true,  false),
            (107, 1, 2020,  7, 'article', false, true),
            (108, 1, 2020,  9, 'book',    false, false),
            (109, 1, 2020,  6, 'article', false, false)
        ) t(work_idx, source_id, publication_year,
            referenced_works_count, type, is_paratext, is_retracted)
    """)
    wks = str(tmp_path / 'works.parquet')
    db.execute(f"COPY works TO '{wks}'")

    # authorships
    db.execute("""
        CREATE TABLE authorships AS FROM (VALUES
            (101, 1001, 201),
            (101, 1001, 202),
            (101, 1002, 203),
            (102, 1003, 204),
            (103, 1004, 205),
            (103, 1005, 206),
            (109, 1006, 207)
        ) t(work_idx, author_idx, institution_idx)
    """)
    auth = str(tmp_path / 'authorships.parquet')
    db.execute(f"COPY authorships TO '{auth}'")

    # topics
    db.execute("""
        CREATE TABLE topics AS FROM (VALUES
            (101, 1, 0.9, 11),
            (101, 2, 0.6, 13),
            (102, 3, 0.8, 11),
            (103, 4, 0.7, 17)
        ) t(work_idx, topic_idx, score, field_idx)
    """)
    top = str(tmp_path / 'topics.parquet')
    db.execute(f"COPY topics TO '{top}'")

    db.close()
    return wks, src, inst, auth, top


def run(tmp_path):
    wks, src, inst, auth, top = make_test_parquets(tmp_path)
    out = str(tmp_path / 'flat_works.parquet')
    with duckdb.connect() as db:
        build_flat_works(db, wks, src, auth, inst, top, out)
    return out


# ─── schema ───────────────────────────────────────────────────────────────────

def test_output_schema(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        cols = {r[0] for r in db.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()}
    expected = {
        'work_idx', 'publication_year', 'source_idx',
        'institution_idx', 'inst_weight', 'direct_inst_weight',
        'field_idx', 'field_weight', 'referenced_works_count',
    }
    assert cols == expected


# ─── filters ─────────────────────────────────────────────────────────────────

def test_only_valid_works_retained(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        works = set(db.execute(f"SELECT DISTINCT work_idx FROM '{out}'").df()['work_idx'])
    assert works == {101, 102, 103}


def test_year_range(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        lo, hi = db.execute(
            f"SELECT MIN(publication_year), MAX(publication_year) FROM '{out}'"
        ).fetchone()
    assert lo >= 2016
    assert hi <= 2025


def test_work_without_topics_excluded(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        works = set(db.execute(f"SELECT DISTINCT work_idx FROM '{out}'").df()['work_idx'])
    assert 109 not in works


# ─── inst_weight (author-fractional) ─────────────────────────────────────────

def test_inst_weight_sums_to_one_per_work_field(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        df = db.execute(f"""
            SELECT work_idx, field_idx, ROUND(SUM(inst_weight), 9) AS total
            FROM '{out}' GROUP BY work_idx, field_idx
        """).df()
    for _, row in df.iterrows():
        assert abs(row['total'] - 1.0) < 1e-6, \
            f"inst_weight sum={row['total']} for work={row['work_idx']} field={row['field_idx']}"


def test_inst_weight_values_work101(tmp_path):
    """
    work 101: 2 authors; author 1001 has 2 insts, author 1002 has 1.
      inst 201: 1/(2*2) = 0.25
      inst 202: 1/(2*2) = 0.25
      inst 203: 1/(2*1) = 0.50
    """
    out = run(tmp_path)
    with duckdb.connect() as db:
        rows = db.execute(f"""
            SELECT institution_idx, inst_weight
            FROM '{out}' WHERE work_idx = 101
            GROUP BY institution_idx, inst_weight
            ORDER BY institution_idx
        """).fetchall()
    w = {r[0]: r[1] for r in rows}
    assert abs(w[201] - 0.25) < 1e-9
    assert abs(w[202] - 0.25) < 1e-9
    assert abs(w[203] - 0.50) < 1e-9


# ─── direct_inst_weight (institution-fractional) ──────────────────────────────

def test_direct_inst_weight_sums_to_one_per_work_field(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        df = db.execute(f"""
            SELECT work_idx, field_idx, ROUND(SUM(direct_inst_weight), 9) AS total
            FROM '{out}' GROUP BY work_idx, field_idx
        """).df()
    for _, row in df.iterrows():
        assert abs(row['total'] - 1.0) < 1e-6, \
            f"direct_inst_weight sum={row['total']} for work={row['work_idx']} field={row['field_idx']}"


def test_direct_inst_weight_values_work101(tmp_path):
    """work 101 has 3 qualifying institutions → direct_inst_weight = 1/3 each."""
    out = run(tmp_path)
    with duckdb.connect() as db:
        rows = db.execute(f"""
            SELECT institution_idx, direct_inst_weight
            FROM '{out}' WHERE work_idx = 101
            GROUP BY institution_idx, direct_inst_weight
            ORDER BY institution_idx
        """).fetchall()
    for _, w in rows:
        assert abs(w - 1/3) < 1e-9


# ─── field_weight ─────────────────────────────────────────────────────────────

def test_field_weight_sums_to_one_per_work_institution(tmp_path):
    out = run(tmp_path)
    with duckdb.connect() as db:
        df = db.execute(f"""
            SELECT work_idx, institution_idx, ROUND(SUM(field_weight), 9) AS total
            FROM '{out}' GROUP BY work_idx, institution_idx
        """).df()
    for _, row in df.iterrows():
        assert abs(row['total'] - 1.0) < 1e-6, \
            f"field_weight sum={row['total']} for work={row['work_idx']} inst={row['institution_idx']}"


def test_field_weight_values_work101(tmp_path):
    """work 101: topics score 0.9 (field 11) + 0.6 (field 13) → weights 0.6, 0.4."""
    out = run(tmp_path)
    with duckdb.connect() as db:
        rows = db.execute(f"""
            SELECT field_idx, field_weight FROM '{out}'
            WHERE work_idx = 101
            GROUP BY field_idx, field_weight ORDER BY field_idx
        """).fetchall()
    w = {r[0]: r[1] for r in rows}
    assert abs(w[11] - 0.6) < 1e-9
    assert abs(w[13] - 0.4) < 1e-9
