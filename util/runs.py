"""
util/runs.py — Run dataclass for spectral ranking parameter sets.
"""

from dataclasses import dataclass, field as dc_field


@dataclass
class Run:
    """
    Parameters for one spectral ranking run.

    window    : '{year_start}_{year_end}', e.g. '2020_2024'
    field_idx : OA field index 11–36
    tau_s     : source retention threshold in weighted works per year
                actual cutoff = tau_s * window_years applied to SUM(field_weight)
    tau_u     : institution retention threshold in weighted works per year
                actual cutoff = tau_u * window_years applied to SUM(field_weight * inst_weight)
    m         : block mask (m_SS, m_SI, m_IS, m_II) ∈ {0,1}^4
                  (0,1,1,0) bipartite SI/IS — standard for joint source+institution ranking
                  (1,0,0,0) source-only via source→source citations
                  (0,0,0,1) institution-only
                  (1,1,1,1) full joint (chi is material)
    alpha     : Katz damping ∈ (0,1]; 1.0 = pure Perron eigenvector
    rho       : 0 = fixed-count (R̄/Rᵢ — each citer work contributes equally)
                1 = full-count (each reference contributes equally)
    chi       : source–institution mixing weight ∈ [0,1]; -1 = χ* = Nᵤ/(Nₛ+Nᵤ)
                Only material for m=(1,1,1,1).
    mu_type   : '' for alpha=1 (Perron); 'uniform' or 'unit_scaled' for alpha<1
    label     : descriptive label for this run
    """
    window:    str
    field_idx: int
    tau_s:     float
    tau_u:     float
    m:         tuple   # (m_SS, m_SI, m_IS, m_II)
    alpha:     float
    rho:       int
    chi:       float = 0.5
    mu_type:   str   = ''
    label:     str   = ''

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
    def window_years(self) -> int:
        tc0, tc1 = (int(x) for x in self.window.split('_'))
        return tc1 - tc0 + 1

    def tau_s_abs(self) -> float:
        return self.tau_s * self.window_years

    def tau_u_abs(self) -> float:
        return self.tau_u * self.window_years

    def el_path(self, working_dir: str) -> str:
        return f"{working_dir}/el_{self.field_idx}_{self.window}_tauS{self.tau_s}_tauU{self.tau_u}.parquet"

    def sc_path(self, working_dir: str) -> str:
        return f"{working_dir}/field_source_cands_{self.window}.parquet"

    def ic_path(self, working_dir: str) -> str:
        return f"{working_dir}/field_inst_cands_{self.window}.parquet"
