"""Tests for util.runs.Run dataclass."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from util.runs import Run


BASELINE = dict(
    tc0=2020, tc1=2024, field_idx=14,
    tau_s=20.0, tau_u=20.0,
    m=(0, 1, 1, 0), alpha=1.0, rho=0,
    label='baseline',
)


def test_window_years():
    r = Run(**BASELINE)
    assert r.window_years == 5


def test_window_property():
    r = Run(**BASELINE)
    assert r.window == '2020_2024'


def test_window_years_ten():
    r = Run(**{**BASELINE, 'tc0': 2016, 'tc1': 2025})
    assert r.window_years == 10


def test_tau_s_abs():
    r = Run(**BASELINE)
    assert r.tau_s_abs() == pytest.approx(100.0)


def test_tau_u_abs():
    r = Run(**{**BASELINE, 'tau_u': 15.0})
    assert r.tau_u_abs() == pytest.approx(75.0)


def test_tau_abs_scales_with_window():
    r = Run(**{**BASELINE, 'tc0': 2016, 'tc1': 2025, 'tau_s': 20.0})
    assert r.tau_s_abs() == pytest.approx(200.0)


def test_validation_alpha1_no_mu():
    r = Run(**BASELINE)          # alpha=1.0, mu_type='' — valid
    assert r.mu_type == ''


def test_validation_alpha_lt1_requires_mu():
    with pytest.raises(ValueError, match='mu_type'):
        Run(**{**BASELINE, 'alpha': 0.85, 'mu_type': ''})


def test_validation_alpha1_no_mu_type():
    with pytest.raises(ValueError, match='mu_type'):
        Run(**{**BASELINE, 'alpha': 1.0, 'mu_type': 'uniform'})


def test_alpha_lt1_with_mu_valid():
    r = Run(**{**BASELINE, 'alpha': 0.85, 'mu_type': 'uniform'})
    assert r.alpha == 0.85
    assert r.mu_type == 'uniform'


def test_el_path():
    r = Run(**BASELINE)
    p = r.el_path('/work')
    assert p == '/work/el_14_2020_2024_baseline.parquet'


def test_sc_path():
    r = Run(**BASELINE)
    assert r.sc_path('/work') == '/work/field_source_cands_2020_2024.parquet'


def test_ic_path():
    r = Run(**BASELINE)
    assert r.ic_path('/work') == '/work/field_inst_cands_2020_2024.parquet'
