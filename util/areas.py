"""
util/areas.py — Single source of truth for the 5 Broad Research Areas (AREA5).

Defines the 5-item top layer bridging:
  (a) Australian ANZSRC FOR 2020 2-digit Divisions (30–52)
  (b) OpenAlex Fields (11–36)

Areas:
  1: MCS  Mathematics, Computing and Information Sciences
  2: PSE  Physical Sciences and Engineering
  3: LES  Life, Agricultural and Environmental Sciences (includes Veterinary / FOR 30)
  4: BHS  Biomedical and Health Sciences (human clinical & biomedical)
  5: SSH  Social Sciences, Humanities, Arts and Business
  (6: IND Indigenous Studies — recognized ANZSRC Division 45, unpopulated from OA topic hierarchy)
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Area:
    id: int
    code: str
    name: str
    short_name: str
    multiline_name: str
    mnemonic: str
    slug: str
    color: str
    oax_fields: tuple[int, ...]
    for_divisions: tuple[int, ...]


AREAS: tuple[Area, ...] = (
    Area(
        id=1,
        code='MCS',
        name='Mathematics, Computing and Information Sciences',
        short_name='Maths & Computing',
        multiline_name='Mathematics and\nComputer Science',
        mnemonic='Maths&CS',
        slug='L1_MathCS',
        color='#377eb8',
        oax_fields=(17, 26),
        for_divisions=(46, 49),
    ),
    Area(
        id=2,
        code='PSE',
        name='Physical Sciences and Engineering',
        short_name='Physical & Engineering',
        multiline_name='Physical Sciences\nand Engineering',
        mnemonic='Phys&Eng',
        slug='L2_PhysEng',
        color='#e41a1c',
        oax_fields=(15, 16, 21, 22, 25, 31),
        for_divisions=(33, 34, 40, 51),
    ),
    Area(
        id=3,
        code='LES',
        name='Life, Agricultural and Environmental Sciences',
        short_name='Life & Earth Sciences',
        multiline_name='Life and\nEarth Sciences',
        mnemonic='Life&Earth',
        slug='L3_LifeEarth',
        color='#4daf4a',
        oax_fields=(11, 13, 19, 23, 24, 34),
        for_divisions=(30, 31, 37, 41),
    ),
    Area(
        id=4,
        code='BHS',
        name='Biomedical and Health Sciences',
        short_name='Biomedical & Health',
        multiline_name='Biomedical and\nHealth Sciences',
        mnemonic='Biomed',
        slug='L4_BiomedHealth',
        color='#984ea3',
        oax_fields=(27, 28, 29, 30, 35, 36),
        for_divisions=(32, 42),
    ),
    Area(
        id=5,
        code='SSH',
        name='Social Sciences, Humanities, Arts and Business',
        short_name='Social Sci & Humanities',
        multiline_name='Social Sciences\nand Humanities',
        mnemonic='Soc&Hum',
        slug='L5_SocialHum',
        color='#ff7f00',
        oax_fields=(12, 14, 18, 20, 32, 33),
        for_divisions=(35, 36, 38, 39, 43, 44, 47, 48, 50, 52),
    ),
)

INDIGENOUS_AREA = Area(
    id=6,
    code='IND',
    name='Indigenous Studies',
    short_name='Indigenous Studies',
    multiline_name='Indigenous\nStudies',
    mnemonic='Indig',
    slug='L6_Indigenous',
    color='#8c564b',
    oax_fields=(),
    for_divisions=(45,),
)

ALL_AREAS: tuple[Area, ...] = AREAS + (INDIGENOUS_AREA,)

# ── Primary Lookups ────────────────────────────────────────────────────────────

AREA_BY_ID: dict[int, Area] = {a.id: a for a in ALL_AREAS}
AREA_BY_CODE: dict[str, Area] = {a.code: a for a in ALL_AREAS}

# id (1–5) → tuple of member OA field_idx
AREA_GROUPS: dict[int, tuple[int, ...]] = {a.id: a.oax_fields for a in AREAS}

# id (1–5) → full descriptive name
AREA_NAMES: dict[int, str] = {a.id: a.name for a in AREAS}

# id (1–5) → short display name
AREA_SHORT_NAMES: dict[int, str] = {a.id: a.short_name for a in AREAS}

# id (1–5) → 2-line display name for subplot facets
AREA_MULTILINE_NAMES: dict[int, str] = {a.id: a.multiline_name for a in AREAS}

# id (1–5) → compact mnemonic (for plot column headers)
AREA_MNEMONICS: dict[int, str] = {a.id: a.mnemonic for a in AREAS}

# id (1–5) → 3-letter code
AREA_CODES: dict[int, str] = {a.id: a.code for a in AREAS}

# id (1–5) → URL/filename slug
AREA_SLUGS: dict[int, str] = {a.id: a.slug for a in AREAS}

# id (1–5) → standard hex color
AREA_COLORS: dict[int, str] = {a.id: a.color for a in AREAS}

# code ('MCS', etc.) → id (1–5, IND=6)
AREA_IDX: dict[str, int] = {a.code: a.id for a in ALL_AREAS}

# OA field_idx (11–36) → area id (1–5)
FIELD_TO_AREA_ID: dict[int, int] = {
    fid: a.id for a in AREAS for fid in a.oax_fields
}

# OA field_idx (11–36) → area code ('MCS', 'PSE', etc.)
FIELD_TO_AREA_CODE: dict[int, str] = {
    fid: a.code for a in AREAS for fid in a.oax_fields
}

# ANZSRC FOR 2020 division code (30–52) → area id (1–6)
FOR_DIV_TO_AREA_ID: dict[int, int] = {
    div: a.id for a in ALL_AREAS for div in a.for_divisions
}

# ANZSRC FOR 2020 division code (30–52) → area code
FOR_DIV_TO_AREA_CODE: dict[int, str] = {
    div: a.code for a in ALL_AREAS for div in a.for_divisions
}

# ── Backward-compatibility aliases for CWTS Leiden naming ──────────────────────

LEIDEN_GROUPS = AREA_GROUPS
LEIDEN_NAMES = AREA_NAMES
LEIDEN_NAMES_SHORT = AREA_MNEMONICS
LEIDEN_GROUP = FIELD_TO_AREA_ID
LEIDEN_LABEL = AREA_SHORT_NAMES
LEIDEN_SLUG = AREA_SLUGS
LEIDEN_COLOUR = AREA_COLORS
