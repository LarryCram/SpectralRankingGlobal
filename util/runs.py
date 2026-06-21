"""
util/runs.py — Run dataclass and GlobalSettings for SpectralRankingGlobal.
"""

from dataclasses import dataclass

VALID_M = frozenset({'1000', '0001', '0110', '1111'})


@dataclass(frozen=True)
class GlobalSettings:
    """
    Global pipeline settings loaded from settings.yaml.
    Consumed in main() only — never passed into ranking functions.
    """
    year_min:                 int
    year_max:                 int
    source_types:             tuple
    work_types:               tuple
    institution_types:        tuple
    memory_limit:             str
    preserve_insertion_order: bool
    katz_tol:                 float
    katz_max_iter:            int
    field_first:              int
    field_last:               int

    @property
    def all_fields(self) -> range:
        return range(self.field_first, self.field_last + 1)


@dataclass
class Run:
    """
    Parameters for one spectral ranking experiment.

    tc0, tc1  : census window — publication years of citing works (inclusive)
    tt0, tt1  : target window — publication years of cited works (0 = same as tc0/tc1)
    tau_s     : source retention threshold in weighted works per year
    tau_u     : institution retention threshold in weighted works per year
    m         : block mask as tuple (m_SS, m_SI, m_IS, m_II); valid: see VALID_M
    alpha     : Katz damping; 1.0 = pure Perron eigenvector
    rho       : 0 = fixed-count (R̄/Rᵢ); 1 = full-count
    chi       : source–institution mixing; -1 = χ* = Nᵤ/(Nₛ+Nᵤ)
    mu_type   : '' (alpha=1), 'uniform', or 'unit_scaled' (alpha<1)
    label     : user-defined run name; used in all output filenames
    field_idx : OA field 11–36; set per-field by the runner via dataclasses.replace()

    Unimplemented flags (wired as int=0; see CLAUDE.md TODO section):
    epsilon   : 0 = standard; 1 = include cross-boundary sentinel units
    omega     : 0 = author-fractional inst weight; 1 = direct 1/N_inst weight
    beta      : 0 = include unit self-references; 1 = exclude
    """
    tc0:       int
    tc1:       int
    tau_s:     float
    tau_u:     float
    m:         tuple
    alpha:     float
    rho:       int
    chi:       float  = 0.5
    mu_type:   str    = ''
    label:     str    = ''
    field_idx: int    = 0     # set per-field by runner; 0 = not yet assigned
    tt0:       int    = 0
    tt1:       int    = 0
    epsilon:   int    = 0     # TODO: sentinel cross-boundary flag
    omega:     int    = 0     # TODO: institution weighting mode
    beta:      int    = 0     # TODO: unit self-reference exclusion flag

    def __post_init__(self):
        if self.alpha < 1.0 and not self.mu_type:
            raise ValueError(
                f"mu_type must be 'uniform' or 'unit_scaled' when alpha={self.alpha}"
            )
        if self.alpha == 1.0 and self.mu_type:
            raise ValueError(
                f"mu_type must be '' when alpha=1.0, got '{self.mu_type}'"
            )

    @property
    def window(self) -> str:
        return f"{self.tc0}_{self.tc1}"

    @property
    def window_years(self) -> int:
        return self.tc1 - self.tc0 + 1

    @property
    def run_id(self) -> str:
        return f"{self.window}_{self.label}"

    def tau_s_abs(self) -> float:
        return self.tau_s * self.window_years

    def tau_u_abs(self) -> float:
        return self.tau_u * self.window_years

    def el_path(self, working_dir: str) -> str:
        return f"{working_dir}/el_{self.field_idx}_{self.window}_{self.label}.parquet"

    def sc_path(self, working_dir: str) -> str:
        return f"{working_dir}/field_source_cands_{self.window}.parquet"

    def ic_path(self, working_dir: str) -> str:
        return f"{working_dir}/field_inst_cands_{self.window}.parquet"

    def rankings_path(self, working_dir: str) -> str:
        return f"{working_dir}/rankings_{self.field_idx}_{self.window}_{self.label}.parquet"

    def diag_path(self, working_dir: str) -> str:
        return (f"{working_dir}/rankings_{self.field_idx}"
                f"_{self.window}_{self.label}_diag.json")
