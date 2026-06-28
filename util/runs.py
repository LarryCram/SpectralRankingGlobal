"""
util/runs.py — Run dataclass and GlobalSettings for SpectralRankingGlobal.
"""

from dataclasses import dataclass, field

VALID_M = frozenset({'1000', '0001', '0110', '1111'})

# Named country sets — combine with set operators to define blocs
CIA  = frozenset({'CN', 'IN', 'US'})        # China, India, America
CIAA = frozenset({'AU', 'CN', 'IN', 'US'})  # + Australia
AU   = frozenset({'AU'})

# CWTS Leiden main field index → OA field_idx members
LEIDEN_GROUPS: dict[int, tuple[int, ...]] = {
    1: (17, 26),
    2: (15, 16, 21, 22, 25, 31),
    3: (11, 13, 19, 23, 24),
    4: (27, 28, 29, 30, 34, 35, 36),
    5: (12, 14, 18, 20, 32, 33),
}

LEIDEN_NAMES: dict[int, str] = {
    1: 'Mathematics and Computer Science',
    2: 'Physical Sciences and Engineering',
    3: 'Life and Earth Sciences',
    4: 'Biomedical and Health Sciences',
    5: 'Social Sciences and Humanities',
}

# Abbreviated display names for OA fields (field_idx 11–36)
FIELD_NAMES: dict[int, str] = {
    11: 'Ag&Bio',      12: 'Arts&Hum',    13: 'Biochem',     14: 'Business',
    15: 'ChemEng',     16: 'Chemistry',   17: 'CompSci',     18: 'Decision',
    19: 'Earth',       20: 'Economics',   21: 'Energy',      22: 'Engineering',
    23: 'EnvSci',      24: 'Immunol',     25: 'Materials',   26: 'Maths',
    27: 'Medicine',    28: 'Neurosci',    29: 'Nursing',     30: 'Pharma',
    31: 'Physics',     32: 'Psychology',  33: 'SocSci',      34: 'Vet',
    35: 'Dentistry',   36: 'HealthProf',
}

# (file_label, blocs_key) pairs for country-bloc runs
BLOC_RUNS: list[tuple[str, str]] = [
    ('baseline',    'WORLD'),          # all countries — no filter
    ('OECDG20',     'OECDG20'),
    ('OECDG20CIA',  'OECDG20-CIA'),
    ('CIAA',        'CIAA'),
    ('BASELINECIA', 'BASELINE-CIA'),   # WORLD − CIA
]


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
    blocs:                    dict   = field(default_factory=dict)  # bloc_name → tuple of ISO-3166-1-alpha-2 codes

    @property
    def all_fields(self) -> range:
        return range(self.field_first, self.field_last + 1)

    @property
    def all_leiden_fields(self) -> range:
        return range(1, 6)


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

    epsilon   : 0 = standard; 1 = add cross-field sentinel units (SX/IX idx=1)
                  absorb cross-field citations; sentinel v-scores masked to NaN
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
    epsilon:   int    = 0
    omega:     int    = 0
    beta:      int    = 0
    bloc:      str    = ''

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
    def is_leiden(self) -> bool:
        """True when field_idx is a Leiden main field index (1–5)."""
        return 1 <= self.field_idx <= 5

    @property
    def is_subfield(self) -> bool:
        """True when field_idx is an OA subfield index (≥ 1000)."""
        return self.field_idx >= 1000

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
        if self.is_leiden:
            return f"{working_dir}/leiden_source_cands_{self.window}.parquet"
        if self.is_subfield:
            return f"{working_dir}/subfield_source_cands_{self.window}.parquet"
        return f"{working_dir}/field_source_cands_{self.window}.parquet"

    def ic_path(self, working_dir: str) -> str:
        if self.is_leiden:
            return f"{working_dir}/leiden_inst_cands_{self.window}.parquet"
        if self.is_subfield:
            return f"{working_dir}/subfield_inst_cands_{self.window}.parquet"
        return f"{working_dir}/field_inst_cands_{self.window}.parquet"

    def rankings_path(self, working_dir: str) -> str:
        return f"{working_dir}/rankings_{self.field_idx}_{self.window}_{self.label}.parquet"

    def diag_path(self, working_dir: str) -> str:
        return (f"{working_dir}/rankings_{self.field_idx}"
                f"_{self.window}_{self.label}_diag.json")
