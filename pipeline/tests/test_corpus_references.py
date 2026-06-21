"""
test_corpus_references.py — Tests for build_flat_works.py stage 1b.

build_corpus_references(db, fw_path, refs_glob, out_path) → int

Contract:
  - OUTPUT = { (citer_idx, cited_idx) ∈ refs : citer_idx ∈ corpus AND cited_idx ∈ corpus }
  - Return value = number of rows written
  - A pair is excluded if EITHER work is absent from flat_works
  - Self-references (citer == cited) are included when the work is in corpus
    (exclusion of self-references is beta=1's job downstream, not this stage)
"""

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from build_flat_works import build_corpus_references


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _write_flat_works(db: duckdb.DuckDBPyConnection,
                      tmp_path: Path,
                      work_ids: list[int]) -> str:
    """Minimal flat_works parquet — only work_idx is used by build_corpus_references."""
    if work_ids:
        vals = ', '.join(f'({w}::BIGINT)' for w in work_ids)
        db.execute(
            f"CREATE OR REPLACE TABLE _fw AS "
            f"SELECT * FROM (VALUES {vals}) t(work_idx)"
        )
    else:
        db.execute("CREATE OR REPLACE TABLE _fw (work_idx BIGINT)")
    path = str(tmp_path / 'flat_works.parquet')
    db.execute(f"COPY _fw TO '{path}'")
    return path


def _write_refs(db: duckdb.DuckDBPyConnection,
                tmp_path: Path,
                pairs: list[tuple[int, int]],
                name: str = 'refs.parquet') -> str:
    """Write OA-style references parquet with (citer_idx, cited_idx) pairs."""
    if pairs:
        vals = ', '.join(f'({c}::BIGINT, {d}::BIGINT)' for c, d in pairs)
        db.execute(
            f"CREATE OR REPLACE TABLE _refs AS "
            f"SELECT * FROM (VALUES {vals}) t(citer_idx, cited_idx)"
        )
    else:
        db.execute("CREATE OR REPLACE TABLE _refs (citer_idx BIGINT, cited_idx BIGINT)")
    path = str(tmp_path / name)
    db.execute(f"COPY _refs TO '{path}'")
    return path


def _read_pairs(db: duckdb.DuckDBPyConnection, path: str) -> set[tuple[int, int]]:
    rows = db.execute(f"SELECT citer_idx, cited_idx FROM '{path}'").fetchall()
    return {(int(r[0]), int(r[1])) for r in rows}


# ─── Schema ───────────────────────────────────────────────────────────────────

class TestSchema:

    def test_output_has_citer_idx_column(self, tmp_path):
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2])
            ref = _write_refs(db, tmp_path, [(1, 2)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            cols = {r[0] for r in db.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()}
        assert 'citer_idx' in cols

    def test_output_has_cited_idx_column(self, tmp_path):
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2])
            ref = _write_refs(db, tmp_path, [(1, 2)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            cols = {r[0] for r in db.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()}
        assert 'cited_idx' in cols

    def test_output_has_exactly_two_columns(self, tmp_path):
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2])
            ref = _write_refs(db, tmp_path, [(1, 2)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            cols = db.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()
        assert len(cols) == 2


# ─── Return value ─────────────────────────────────────────────────────────────

class TestReturnValue:

    def test_returns_row_count(self, tmp_path):
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2, 3])
            ref = _write_refs(db, tmp_path, [(1, 2), (2, 3), (3, 1)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 3

    def test_returns_zero_when_no_pairs_qualify(self, tmp_path):
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1])
            ref = _write_refs(db, tmp_path, [(99, 100)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 0

    def test_return_matches_actual_row_count(self, tmp_path):
        """Return value must equal the physical row count in the parquet."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2, 3, 4])
            ref = _write_refs(db, tmp_path, [(1, 2), (1, 99), (99, 2), (3, 4)])
            out = str(tmp_path / 'cr.parquet')
            returned = build_corpus_references(db, fw, ref, out)
            actual = db.execute(f"SELECT COUNT(*) FROM '{out}'").fetchone()[0]
        assert returned == actual


# ─── Inclusion / exclusion logic ──────────────────────────────────────────────

class TestFilter:

    def test_both_in_corpus_included(self, tmp_path):
        """(W1→W2): W1 and W2 both in flat_works → pair is included."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2])
            ref = _write_refs(db, tmp_path, [(1, 2)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            pairs = _read_pairs(db, out)
        assert (1, 2) in pairs

    def test_citer_not_in_corpus_excluded(self, tmp_path):
        """(W99→W2): citer W99 absent from flat_works → pair excluded."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [2])        # W99 absent
            ref = _write_refs(db, tmp_path, [(99, 2)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            pairs = _read_pairs(db, out)
        assert (99, 2) not in pairs
        assert len(pairs) == 0

    def test_cited_not_in_corpus_excluded(self, tmp_path):
        """(W1→W99): cited W99 absent from flat_works → pair excluded."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1])        # W99 absent
            ref = _write_refs(db, tmp_path, [(1, 99)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            pairs = _read_pairs(db, out)
        assert (1, 99) not in pairs
        assert len(pairs) == 0

    def test_neither_in_corpus_excluded(self, tmp_path):
        """(W98→W99): neither work in flat_works → pair excluded."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2])     # neither 98 nor 99
            ref = _write_refs(db, tmp_path, [(98, 99)])
            out = str(tmp_path / 'cr.parquet')
            build_corpus_references(db, fw, ref, out)
            pairs = _read_pairs(db, out)
        assert len(pairs) == 0

    def test_mixed_batch_only_valid_pairs_survive(self, tmp_path):
        """
        Corpus = {W1, W2, W3}.  References:
          (W1, W2)  → INCLUDED  (both in corpus)
          (W1, W3)  → INCLUDED
          (W1, W99) → excluded  (W99 absent)
          (W99, W2) → excluded  (W99 absent)
          (W88, W77)→ excluded  (both absent)
        """
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2, 3])
            ref = _write_refs(db, tmp_path,
                              [(1, 2), (1, 3), (1, 99), (99, 2), (88, 77)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
            pairs = _read_pairs(db, out)
        assert n == 2
        assert pairs == {(1, 2), (1, 3)}

    def test_all_refs_valid_all_included(self, tmp_path):
        """When all referenced works are in flat_works, nothing is dropped."""
        corpus = [10, 20, 30]
        all_refs = [(10, 20), (20, 30), (30, 10), (10, 30), (20, 10)]
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, corpus)
            ref = _write_refs(db, tmp_path, all_refs)
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == len(all_refs)


# ─── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_flat_works_gives_empty_output(self, tmp_path):
        """No works in corpus → no references can qualify."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [])
            ref = _write_refs(db, tmp_path, [(1, 2), (3, 4)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 0

    def test_empty_refs_gives_empty_output(self, tmp_path):
        """No OA references → output is empty regardless of corpus size."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2, 3])
            ref = _write_refs(db, tmp_path, [])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 0

    def test_self_reference_included_when_work_in_corpus(self, tmp_path):
        """
        (W1→W1): self-citation where W1 is in corpus must be INCLUDED.
        Stage 1b does not filter self-references; that is beta=1's job in stage 4a.
        """
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1])
            ref = _write_refs(db, tmp_path, [(1, 1)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
            pairs = _read_pairs(db, out)
        assert n == 1
        assert (1, 1) in pairs

    def test_self_reference_excluded_when_work_absent(self, tmp_path):
        """(W99→W99): self-citation where W99 is NOT in corpus → excluded."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1, 2])
            ref = _write_refs(db, tmp_path, [(99, 99)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 0

    def test_single_corpus_work_no_refs_to_self(self, tmp_path):
        """Corpus has W1 only; all external refs are dropped."""
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, [1])
            ref = _write_refs(db, tmp_path, [(1, 2), (2, 1), (3, 4)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 0

    def test_duplicate_work_ids_in_flat_works_handled(self, tmp_path):
        """DISTINCT in the subquery means duplicate rows in flat_works don't cause duplicate output."""
        with duckdb.connect() as db:
            # Manually create flat_works with duplicate work_idx rows
            db.execute("""
                CREATE OR REPLACE TABLE _fw_dup AS
                SELECT * FROM (VALUES
                    (1::BIGINT), (1::BIGINT), (2::BIGINT)
                ) t(work_idx)
            """)
            fw_dup = str(tmp_path / 'fw_dup.parquet')
            db.execute(f"COPY _fw_dup TO '{fw_dup}'")
            ref = _write_refs(db, tmp_path, [(1, 2)])
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw_dup, ref, out)
        assert n == 1   # pair (1,2) appears exactly once despite duplicate W1 rows

    def test_large_corpus_works_correctly(self, tmp_path):
        """Sanity check with 1 000 corpus works and selective references."""
        corpus = list(range(1, 1001))          # 1–1000
        valid_refs   = [(i, i + 1) for i in range(1, 100)]      # 99 pairs all in corpus
        invalid_refs = [(i, i + 1000) for i in range(1, 50)]    # 49 pairs cited out of corpus
        all_refs = valid_refs + invalid_refs
        with duckdb.connect() as db:
            fw  = _write_flat_works(db, tmp_path, corpus)
            ref = _write_refs(db, tmp_path, all_refs)
            out = str(tmp_path / 'cr.parquet')
            n = build_corpus_references(db, fw, ref, out)
        assert n == 99
