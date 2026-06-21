"""
test_run_dataclass.py — Tests for util/runs.py (Run, GlobalSettings, VALID_M).

Every advertised property, path method, default value, and validation rule
for the Run dataclass is verified here against known expected values.
"""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from util.runs import Run, GlobalSettings, VALID_M, LEIDEN_GROUPS, LEIDEN_NAMES

# ─── Helpers ──────────────────────────────────────────────────────────────────

_BASE = dict(tc0=2020, tc1=2024, tau_s=10.0, tau_u=10.0,
             m=(0, 1, 1, 0), alpha=1.0, rho=0)

def make(**kw) -> Run:
    return Run(**{**_BASE, **kw})


# ─── window / window_years / run_id ───────────────────────────────────────────

class TestWindowProperties:

    def test_window_string(self):
        assert make().window == '2020_2024'

    def test_window_years_five(self):
        assert make().window_years == 5

    def test_window_single_year(self):
        r = make(tc0=2022, tc1=2022)
        assert r.window == '2022_2022'
        assert r.window_years == 1

    def test_window_ten_years(self):
        r = make(tc0=2015, tc1=2024)
        assert r.window == '2015_2024'
        assert r.window_years == 10

    def test_run_id_combines_window_and_label(self):
        assert make(label='baseline').run_id == '2020_2024_baseline'

    def test_run_id_empty_label(self):
        assert make(label='').run_id == '2020_2024_'

    def test_run_id_custom_label(self):
        assert make(label='tau20-rho1').run_id == '2020_2024_tau20-rho1'


# ─── tau_s_abs / tau_u_abs ────────────────────────────────────────────────────

class TestTauAbs:

    def test_tau_s_abs_five_year_window(self):
        assert make(tau_s=10.0).tau_s_abs() == 50.0   # 10/yr × 5yr

    def test_tau_u_abs_five_year_window(self):
        assert make(tau_u=20.0).tau_u_abs() == 100.0  # 20/yr × 5yr

    def test_tau_abs_one_year_window(self):
        r = make(tc0=2024, tc1=2024, tau_s=15.0, tau_u=8.0)
        assert r.tau_s_abs() == 15.0
        assert r.tau_u_abs() == 8.0

    def test_tau_abs_fractional(self):
        r = make(tau_s=5.5)
        assert abs(r.tau_s_abs() - 27.5) < 1e-9   # 5.5 × 5

    def test_tau_s_tau_u_independent(self):
        r = make(tau_s=5.0, tau_u=30.0)
        assert r.tau_s_abs() == 25.0
        assert r.tau_u_abs() == 150.0


# ─── path methods ─────────────────────────────────────────────────────────────

class TestPathMethods:

    def test_sc_path_uses_window_only(self):
        r = make()
        assert r.sc_path('/w') == '/w/field_source_cands_2020_2024.parquet'

    def test_ic_path_uses_window_only(self):
        r = make()
        assert r.ic_path('/w') == '/w/field_inst_cands_2020_2024.parquet'

    def test_sc_ic_paths_independent_within_field_range(self):
        r1 = replace(make(), field_idx=11)
        r2 = replace(make(), field_idx=36)
        assert r1.sc_path('/w') == r2.sc_path('/w')
        assert r1.ic_path('/w') == r2.ic_path('/w')

    def test_leiden_sc_path(self):
        r = replace(make(), field_idx=1)
        assert r.sc_path('/w') == '/w/leiden_source_cands_2020_2024.parquet'

    def test_leiden_ic_path(self):
        r = replace(make(), field_idx=3)
        assert r.ic_path('/w') == '/w/leiden_inst_cands_2020_2024.parquet'

    def test_subfield_sc_path(self):
        r = replace(make(), field_idx=1700)
        assert r.sc_path('/w') == '/w/subfield_source_cands_2020_2024.parquet'

    def test_subfield_ic_path(self):
        r = replace(make(), field_idx=2746)
        assert r.ic_path('/w') == '/w/subfield_inst_cands_2020_2024.parquet'

    def test_el_path_includes_field_window_label(self):
        r = replace(make(label='run1'), field_idx=17)
        assert r.el_path('/w') == '/w/el_17_2020_2024_run1.parquet'

    def test_rankings_path_includes_field_window_label(self):
        r = replace(make(label='run1'), field_idx=27)
        assert r.rankings_path('/w') == '/w/rankings_27_2020_2024_run1.parquet'

    def test_diag_path_includes_field_window_label(self):
        r = replace(make(label='run1'), field_idx=27)
        assert r.diag_path('/w') == '/w/rankings_27_2020_2024_run1_diag.json'

    def test_el_path_changes_with_label(self):
        r1 = replace(make(label='a'), field_idx=17)
        r2 = replace(make(label='b'), field_idx=17)
        assert r1.el_path('/w') != r2.el_path('/w')

    def test_rankings_path_changes_with_field(self):
        r1 = replace(make(label='x'), field_idx=11)
        r2 = replace(make(label='x'), field_idx=12)
        assert r1.rankings_path('/w') != r2.rankings_path('/w')


# ─── is_leiden / is_subfield ──────────────────────────────────────────────────

class TestRunLevelProperties:

    def test_is_leiden_true_for_1_to_5(self):
        for i in range(1, 6):
            assert replace(make(), field_idx=i).is_leiden

    def test_is_leiden_false_for_oa_field(self):
        for i in [11, 17, 26, 36]:
            assert not replace(make(), field_idx=i).is_leiden

    def test_is_leiden_false_for_zero(self):
        assert not make().is_leiden  # default field_idx=0

    def test_is_subfield_true_for_large_idx(self):
        for i in [1100, 1700, 2746, 3616]:
            assert replace(make(), field_idx=i).is_subfield

    def test_is_subfield_false_for_oa_field(self):
        for i in [11, 17, 36]:
            assert not replace(make(), field_idx=i).is_subfield

    def test_is_subfield_false_for_leiden(self):
        for i in range(1, 6):
            assert not replace(make(), field_idx=i).is_subfield

    def test_is_leiden_and_is_subfield_mutually_exclusive(self):
        for i in range(1, 6):
            r = replace(make(), field_idx=i)
            assert not (r.is_leiden and r.is_subfield)
        for i in [11, 17, 1100, 1700]:
            r = replace(make(), field_idx=i)
            assert not (r.is_leiden and r.is_subfield)


# ─── LEIDEN_GROUPS / LEIDEN_NAMES ─────────────────────────────────────────────

class TestLeidenConstants:

    def test_leiden_groups_has_five_entries(self):
        assert len(LEIDEN_GROUPS) == 5

    def test_leiden_names_has_five_entries(self):
        assert len(LEIDEN_NAMES) == 5

    def test_leiden_groups_covers_all_26_oa_fields(self):
        all_fields = {f for fields in LEIDEN_GROUPS.values() for f in fields}
        assert all_fields == set(range(11, 37))

    def test_leiden_groups_no_overlap(self):
        from itertools import combinations
        groups = list(LEIDEN_GROUPS.values())
        for a, b in combinations(groups, 2):
            assert not set(a) & set(b), f"Overlap between leiden groups: {set(a) & set(b)}"

    def test_leiden_names_keys_match_groups(self):
        assert set(LEIDEN_GROUPS) == set(LEIDEN_NAMES)

    def test_cs_field_in_leiden_1(self):
        assert 17 in LEIDEN_GROUPS[1]

    def test_medicine_in_leiden_4(self):
        assert 27 in LEIDEN_GROUPS[4]


# ─── default field values ─────────────────────────────────────────────────────

class TestDefaults:

    def test_epsilon_default(self):
        assert make().epsilon == 0

    def test_omega_default(self):
        assert make().omega == 0

    def test_beta_default(self):
        assert make().beta == 0

    def test_field_idx_default(self):
        assert make().field_idx == 0

    def test_chi_default(self):
        assert make().chi == 0.5

    def test_mu_type_default(self):
        assert make().mu_type == ''

    def test_label_default(self):
        assert make().label == ''

    def test_tt0_default(self):
        assert make().tt0 == 0

    def test_tt1_default(self):
        assert make().tt1 == 0

    def test_bloc_default(self):
        assert make().bloc == ''

    def test_bloc_is_str(self):
        assert isinstance(make().bloc, str)

    def test_epsilon_omega_beta_are_ints(self):
        r = make()
        assert isinstance(r.epsilon, int)
        assert isinstance(r.omega, int)
        assert isinstance(r.beta, int)


# ─── __post_init__ validation ─────────────────────────────────────────────────

class TestValidation:

    def test_alpha_lt1_empty_mu_type_raises(self):
        with pytest.raises(ValueError, match='mu_type'):
            make(alpha=0.85, mu_type='')

    def test_alpha1_uniform_mu_type_raises(self):
        with pytest.raises(ValueError, match='mu_type'):
            make(alpha=1.0, mu_type='uniform')

    def test_alpha1_unit_scaled_mu_type_raises(self):
        with pytest.raises(ValueError, match='mu_type'):
            make(alpha=1.0, mu_type='unit_scaled')

    def test_alpha_lt1_uniform_ok(self):
        make(alpha=0.85, mu_type='uniform')   # must not raise

    def test_alpha_lt1_unit_scaled_ok(self):
        make(alpha=0.85, mu_type='unit_scaled')   # must not raise

    def test_alpha1_empty_mu_type_ok(self):
        make(alpha=1.0, mu_type='')   # must not raise


# ─── GlobalSettings ───────────────────────────────────────────────────────────

_GS_ARGS = dict(
    year_min=2016, year_max=2025,
    source_types=('journal', 'conference'), work_types=('article', 'review'),
    institution_types=('education',),
    memory_limit='56GB', preserve_insertion_order=False,
    katz_tol=1e-9, katz_max_iter=500,
    field_first=11, field_last=36,
)


class TestGlobalSettings:

    def test_all_fields_starts_at_field_first(self):
        gs = GlobalSettings(**_GS_ARGS)
        assert list(gs.all_fields)[0] == 11

    def test_all_fields_ends_at_field_last(self):
        gs = GlobalSettings(**_GS_ARGS)
        assert list(gs.all_fields)[-1] == 36

    def test_all_fields_length_26(self):
        gs = GlobalSettings(**_GS_ARGS)
        assert len(list(gs.all_fields)) == 26

    def test_all_fields_custom_range(self):
        gs = GlobalSettings(**{**_GS_ARGS, 'field_first': 20, 'field_last': 22})
        assert list(gs.all_fields) == [20, 21, 22]

    def test_all_fields_single(self):
        gs = GlobalSettings(**{**_GS_ARGS, 'field_first': 17, 'field_last': 17})
        assert list(gs.all_fields) == [17]

    def test_all_leiden_fields_is_1_to_5(self):
        gs = GlobalSettings(**_GS_ARGS)
        assert list(gs.all_leiden_fields) == [1, 2, 3, 4, 5]

    def test_frozen(self):
        gs = GlobalSettings(**_GS_ARGS)
        with pytest.raises((AttributeError, TypeError)):
            gs.year_min = 2000   # type: ignore

    def test_source_types_stored_as_tuple(self):
        gs = GlobalSettings(**_GS_ARGS)
        assert isinstance(gs.source_types, tuple)

    def test_preserve_insertion_order_bool(self):
        gs = GlobalSettings(**_GS_ARGS)
        assert gs.preserve_insertion_order is False


# ─── VALID_M ──────────────────────────────────────────────────────────────────

class TestValidM:

    def test_contains_bipartite(self):
        assert '0110' in VALID_M

    def test_contains_source_only(self):
        assert '1000' in VALID_M

    def test_contains_institution_only(self):
        assert '0001' in VALID_M

    def test_contains_full_joint(self):
        assert '1111' in VALID_M

    def test_exactly_four_valid_masks(self):
        assert len(VALID_M) == 4

    def test_rejects_partial_1100(self):
        assert '1100' not in VALID_M

    def test_rejects_all_zero(self):
        assert '0000' not in VALID_M

    def test_rejects_0011(self):
        assert '0011' not in VALID_M

    def test_is_frozenset(self):
        assert isinstance(VALID_M, frozenset)
