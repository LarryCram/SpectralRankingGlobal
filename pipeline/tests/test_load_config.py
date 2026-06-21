"""
test_load_config.py — Tests for util/load_config.py.

Verifies that load_settings() and load_runs() parse every field correctly,
apply the skip filter, validate m against VALID_M, and return the right types.
All tests use temp files so there is no dependency on project config.yaml or params.csv.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from util.load_config import load_settings, load_runs
from util.runs import GlobalSettings, Run, VALID_M

# ─── Fixtures ─────────────────────────────────────────────────────────────────

_YAML = textwrap.dedent("""\
    corpus:
      year_min: 2016
      year_max: 2025
      source_types: [journal, conference, "book series"]
      work_types: [article, review]
      institution_types: [education, nonprofit, government, healthcare, other]
    duckdb:
      memory_limit: "56GB"
      preserve_insertion_order: false
    katz:
      tol: 1.0e-9
      max_iter: 500
    fields:
      first: 11
      last:  36
""")

_CSV_HEADER = (
    'skip,tc0,tc1,tt0,tt1,tau_u,tau_s,rho,omega,m,chi,alpha,mu_type,label,epsilon,beta,bloc\n'
)
_BASELINE = '0,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,baseline,0,0,\n'


def write_yaml(tmp_path) -> Path:
    p = tmp_path / 'settings.yaml'
    p.write_text(_YAML)
    return p


def write_csv(tmp_path, rows) -> Path:
    p = tmp_path / 'params.csv'
    p.write_text(_CSV_HEADER + ''.join(rows))
    return p


# ─── load_settings() ──────────────────────────────────────────────────────────

class TestLoadSettings:

    def test_returns_global_settings(self, tmp_path):
        assert isinstance(load_settings(write_yaml(tmp_path)), GlobalSettings)

    def test_year_min(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).year_min == 2016

    def test_year_max(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).year_max == 2025

    def test_source_types(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert gs.source_types == ('journal', 'conference', 'book series')

    def test_work_types(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert gs.work_types == ('article', 'review')

    def test_institution_types(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert 'education' in gs.institution_types
        assert 'healthcare' in gs.institution_types

    def test_memory_limit(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).memory_limit == '56GB'

    def test_preserve_insertion_order(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).preserve_insertion_order is False

    def test_katz_tol(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).katz_tol == 1e-9

    def test_katz_max_iter(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).katz_max_iter == 500

    def test_field_first(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).field_first == 11

    def test_field_last(self, tmp_path):
        assert load_settings(write_yaml(tmp_path)).field_last == 36

    def test_all_fields_range(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert list(gs.all_fields) == list(range(11, 37))

    def test_types_stored_as_tuples(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert isinstance(gs.source_types, tuple)
        assert isinstance(gs.work_types, tuple)
        assert isinstance(gs.institution_types, tuple)


# ─── load_runs() — skip filter ────────────────────────────────────────────────

class TestSkipFilter:

    def test_skip0_included(self, tmp_path):
        runs = load_runs(write_csv(tmp_path, [_BASELINE]))
        assert len(runs) == 1

    def test_skip1_excluded(self, tmp_path):
        rows = [
            _BASELINE,
            '1,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,skipped,0,0,\n',
        ]
        runs = load_runs(write_csv(tmp_path, rows))
        assert len(runs) == 1
        assert runs[0].label == 'baseline'

    def test_all_skipped_returns_empty(self, tmp_path):
        rows = ['1,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,baseline,0,0,\n']
        assert load_runs(write_csv(tmp_path, rows)) == []

    def test_multiple_active_rows(self, tmp_path):
        rows = [
            '0,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,run1,0,0,\n',
            '0,2020,2024,2020,2024,20,20,0,0,0110,0.5,1.0,,run2,0,0,\n',
        ]
        runs = load_runs(write_csv(tmp_path, rows))
        assert len(runs) == 2
        assert {r.label for r in runs} == {'run1', 'run2'}


# ─── load_runs() — m parsing and validation ───────────────────────────────────

class TestMParsing:

    def test_0110_parsed_to_tuple(self, tmp_path):
        runs = load_runs(write_csv(tmp_path, [_BASELINE]))
        assert runs[0].m == (0, 1, 1, 0)

    def test_1000_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,1000,0.5,1.0,,x,0,0,\n']
        assert load_runs(write_csv(tmp_path, rows))[0].m == (1, 0, 0, 0)

    def test_0001_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0001,0.5,1.0,,x,0,0,\n']
        assert load_runs(write_csv(tmp_path, rows))[0].m == (0, 0, 0, 1)

    def test_1111_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,1111,0.5,1.0,,x,0,0,\n']
        assert load_runs(write_csv(tmp_path, rows))[0].m == (1, 1, 1, 1)

    def test_invalid_m_1100_raises(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,1100,0.5,1.0,,x,0,0,\n']
        with pytest.raises(ValueError, match='invalid m'):
            load_runs(write_csv(tmp_path, rows))

    def test_invalid_m_0000_raises(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0000,0.5,1.0,,x,0,0,\n']
        with pytest.raises(ValueError, match='invalid m'):
            load_runs(write_csv(tmp_path, rows))

    def test_invalid_m_0011_raises(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0011,0.5,1.0,,x,0,0,\n']
        with pytest.raises(ValueError, match='invalid m'):
            load_runs(write_csv(tmp_path, rows))


# ─── load_runs() — numeric column types ───────────────────────────────────────

class TestColumnTypes:

    def _run(self, tmp_path):
        return load_runs(write_csv(tmp_path, [_BASELINE]))[0]

    def test_tc0_is_int(self, tmp_path):
        assert isinstance(self._run(tmp_path).tc0, int)

    def test_tc1_is_int(self, tmp_path):
        assert isinstance(self._run(tmp_path).tc1, int)

    def test_tau_s_is_float(self, tmp_path):
        assert isinstance(self._run(tmp_path).tau_s, float)

    def test_tau_u_is_float(self, tmp_path):
        assert isinstance(self._run(tmp_path).tau_u, float)

    def test_alpha_is_float(self, tmp_path):
        assert isinstance(self._run(tmp_path).alpha, float)

    def test_chi_is_float(self, tmp_path):
        assert isinstance(self._run(tmp_path).chi, float)

    def test_rho_is_int(self, tmp_path):
        assert isinstance(self._run(tmp_path).rho, int)

    def test_omega_is_int(self, tmp_path):
        assert isinstance(self._run(tmp_path).omega, int)

    def test_epsilon_is_int(self, tmp_path):
        assert isinstance(self._run(tmp_path).epsilon, int)

    def test_beta_is_int(self, tmp_path):
        assert isinstance(self._run(tmp_path).beta, int)

    def test_label_is_str(self, tmp_path):
        assert isinstance(self._run(tmp_path).label, str)

    def test_mu_type_is_str(self, tmp_path):
        assert isinstance(self._run(tmp_path).mu_type, str)


# ─── load_runs() — field values ───────────────────────────────────────────────

class TestFieldValues:

    def test_tc0_value(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.tc0 == 2020

    def test_tc1_value(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.tc1 == 2024

    def test_window_property(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.window == '2020_2024'

    def test_run_id_property(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.run_id == '2020_2024_baseline'

    def test_tau_s_value(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.tau_s == 10.0

    def test_tau_u_value(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.tau_u == 10.0

    def test_rho_value(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.rho == 0

    def test_chi_negative_one(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,1111,-1.0,1.0,,chistar,0,0,\n']
        run = load_runs(write_csv(tmp_path, rows))[0]
        assert run.chi == -1.0

    def test_epsilon_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,x,1,0,\n']
        assert load_runs(write_csv(tmp_path, rows))[0].epsilon == 1

    def test_beta_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,x,0,1,\n']
        assert load_runs(write_csv(tmp_path, rows))[0].beta == 1

    def test_omega_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,1,0110,0.5,1.0,,x,0,0,\n']
        assert load_runs(write_csv(tmp_path, rows))[0].omega == 1

    def test_mu_type_empty_string(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.mu_type == ''

    def test_mu_type_uniform(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0110,0.5,0.85,uniform,x,0,0,\n']
        run = load_runs(write_csv(tmp_path, rows))[0]
        assert run.mu_type == 'uniform'

    def test_multiple_runs_tau_differs(self, tmp_path):
        rows = [
            '0,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,run1,0,0,\n',
            '0,2020,2024,2020,2024,20,20,0,0,0110,0.5,1.0,,run2,0,0,\n',
        ]
        r1, r2 = load_runs(write_csv(tmp_path, rows))
        assert r1.tau_s == 10.0
        assert r2.tau_s == 20.0

    def test_returns_run_instances(self, tmp_path):
        runs = load_runs(write_csv(tmp_path, [_BASELINE]))
        assert all(isinstance(r, Run) for r in runs)

    def test_bloc_default_is_empty_string(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert run.bloc == ''

    def test_bloc_parsed(self, tmp_path):
        rows = ['0,2020,2024,2020,2024,10,10,0,0,0110,0.5,1.0,,x,0,0,AU\n']
        assert load_runs(write_csv(tmp_path, rows))[0].bloc == 'AU'

    def test_bloc_is_str(self, tmp_path):
        run = load_runs(write_csv(tmp_path, [_BASELINE]))[0]
        assert isinstance(run.bloc, str)


# ─── load_settings() — blocs ──────────────────────────────────────────────────

class TestBlocs:

    def test_blocs_is_dict(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert isinstance(gs.blocs, dict)

    def test_blocs_oecdg20_contains_au(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert 'AU' in gs.blocs.get('OECDG20', ())

    def test_blocs_oecdg20_cia_excludes_cn_in_us(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        bloc = gs.blocs.get('OECDG20-CIA', ())
        assert 'CN' not in bloc
        assert 'IN' not in bloc
        assert 'US' not in bloc

    def test_blocs_ciaa_has_four_entries(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert gs.blocs.get('CIAA') == ('AU', 'CN', 'IN', 'US')

    def test_blocs_au_is_single_entry(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path))
        assert gs.blocs.get('AU') == ('AU',)

    def test_blocs_missing_file_returns_empty(self, tmp_path):
        gs = load_settings(write_yaml(tmp_path), blocs_path=tmp_path / 'nonexistent.xlsx')
        assert gs.blocs == {}
