# SPECIAL_HCR_HCA_NAMES

Corrections identified in `hca_hcr_name_diag.py` that are absent from
`hcr_match.py` but needed by `hcr_hca_map.py`.  All are **blocking failures**
(zero candidates at every waterfall stage) unless fixed.

Destination: `_apply_corrections()` in `analysis/hcr_match.py`, applied before
`HumanName` parsing so that `last_norm` and `fi` are computed on corrected values.

---

## Category 1 — Umlaut surnames (8 names)

HCR stores the German digraph form; OAX stores the actual umlaut character.
`norm()` uses `unidecode`, which maps `ü→u` but leaves `ue` unchanged:

```
HCR  "Tuereci" → last_norm = "tuereci"
OAX  "Türeci"  → last_norm = "tureci"     ← miss at every stage
```

Fix: convert digraph → umlaut in the Last Name field before parsing, so both
sides reach the same ASCII form via `unidecode`.

| HCR `Last Name`     | Correct form        |
|---------------------|---------------------|
| Tuereci             | Türeci              |
| Poertner            | Pörtner             |
| Baernighausen       | Bärnighausen        |
| Bruening            | Brüning             |
| Humpenoeder         | Humpenöder          |
| Floerke             | Flörke              |
| Mueller-Buschbaum   | Müller-Buschbaum    |
| Goetschi            | Götschi             |

---

## Category 2 — Compound surname (1 name)

HCR splits what OAX stores as one token, so `last_norm` takes the last word only:

```
HCR  "Vander Weele" → last_norm = "weele"
OAX  "VanderWeele"  → last_norm = "vanderweele"   ← miss at every stage
```

| HCR `Last Name` | Correct form  |
|-----------------|---------------|
| Vander Weele    | VanderWeele   |

---

## Category 3 — Preferred-name first names (5 names)

HCR records initials or a different name form; OAX indexes the researcher under
a different first name, so `fi` differs and every waterfall stage misses.

| HCR `First Name`  | Correct form | Affected researcher | Reason                        |
|-------------------|--------------|---------------------|-------------------------------|
| F. D. Richard     | Richard      | Hobbs               | initials prepended; OA fi=r   |
| R. M. Pasco       | Pasco        | Fearon              | initials prepended; OA fi=p   |
| Fraser            | J. Fraser    | Stoddart            | particle initial; OA fi=j     |
| Geoffrey Qiping   | Qiping       | Shen                | preferred is middle; OA fi=q  |
| Ryan              | Adriaan      | Teuling             | OA uses legal first name      |

---

## Category 4 — Degraded but not fatal (found at fi+L anyway)

Not blocking misses; included for completeness.

| HCR `First Name` | Better form | Researcher  | Effect without fix                        |
|------------------|-------------|-------------|-------------------------------------------|
| Ai-Guo           | Aiguo       | Wu          | fa="ai-guo"≠"aiguo" → misses F+L, hits fi+L |
| Konstantin S.    | Kostya S.   | Novoselov   | fa mismatch → misses F+L, hits fi+L      |

---

## Code to add to `_apply_corrections` in `analysis/hcr_match.py`

Insert after the existing field-swap corrections (after the `David Autor` block,
before the postnominal-stripping step).  The three dicts are independent and
can each be a simple `.replace()` call.

```python
    # ── preferred-name first-name corrections ─────────────────────────────────
    # OA indexes these researchers under a different first name / preferred name,
    # causing fi mismatch that breaks every waterfall stage.
    _FIRST_FIXES = {
        'F. D. Richard':   'Richard',    # Hobbs:    OA "Richard Hobbs"    fi r≠f
        'R. M. Pasco':     'Pasco',      # Fearon:   OA "Pasco Fearon"     fi p≠r
        'Fraser':          'J. Fraser',  # Stoddart: OA "J. Fraser Stoddart" fi j≠f
        'Geoffrey Qiping': 'Qiping',     # Shen:     OA "Qiping Shen"      fi q≠g
        'Ryan':            'Adriaan',    # Teuling:  OA uses legal name     fi a≠r
        # Below are degraded (found at fi+L anyway) but cleaner with fix:
        'Ai-Guo':          'Aiguo',      # Wu:       hyphen breaks fa match
        'Konstantin S.':   'Kostya S.',  # Novoselov: OA "Kostya S. Novoselov"
    }
    hcr['First Name'] = hcr['First Name'].replace(_FIRST_FIXES)

    # ── umlaut surname corrections ────────────────────────────────────────────
    # HCR stores German digraph form (ue/ae/oe); OA stores actual umlaut (ü/ä/ö).
    # unidecode("ü")="u" but "ue" stays "ue" → last_norm mismatch blocks all stages.
    _UMLAUT_FIXES = {
        'Tuereci':            'Türeci',
        'Poertner':           'Pörtner',
        'Baernighausen':      'Bärnighausen',
        'Bruening':           'Brüning',
        'Humpenoeder':        'Humpenöder',
        'Floerke':            'Flörke',
        'Mueller-Buschbaum':  'Müller-Buschbaum',
        'Goetschi':           'Götschi',
    }
    hcr['Last Name'] = hcr['Last Name'].replace(_UMLAUT_FIXES)

    # ── compound surname corrections ──────────────────────────────────────────
    # HCR splits what OA stores as one token; last_norm takes the last word only.
    # "Vander Weele" → last_norm="weele" vs OA last_norm="vanderweele".
    _SURNAME_FIXES = {
        'Vander Weele': 'VanderWeele',
    }
    hcr['Last Name'] = hcr['Last Name'].replace(_SURNAME_FIXES)
```

Note: `_apply_corrections` receives the DataFrame before the column rename in
`load_hcr` (the rename happens at line 106, after `_apply_corrections` returns),
so the column names inside the function are `'First Name'` / `'Last Name'`.

---

## Verification

After applying, confirm each name reaches the correct `last_norm` / `fi`:

```python
from util.name_util import clean_name, last_norm, fi as fi_fn
from nameparser import HumanName

expected = [
    # (corrected_first, corrected_last, expected_last_norm, expected_fi)
    ('Richard',    'Hobbs',             'hobbs',             'r'),
    ('Pasco',      'Fearon',            'fearon',            'p'),
    ('J. Fraser',  'Stoddart',          'stoddart',          'j'),
    ('Qiping',     'Shen',              'shen',              'q'),
    ('Adriaan',    'Teuling',           'teuling',           'a'),
    ('Tyler',      'VanderWeele',       'vanderweele',       't'),
    ('Özlem',      'Türeci',            'tureci',            'o'),
    ('Hans-Otto',  'Pörtner',           'portner',           'h'),
    ('Till',       'Bärnighausen',      'barnighausen',      't'),
    ('Jochen',     'Brüning',           'bruning',           'j'),
    ('Florian',    'Humpenöder',        'humpenoder',        'f'),
    ('Martina',    'Flörke',            'florke',            'm'),
    ('Peter',      'Müller-Buschbaum',  'muller-buschbaum',  'p'),
    ('Thomas',     'Götschi',           'gotschi',           't'),
]

for first, last, exp_ln, exp_fi in expected:
    hn   = HumanName(clean_name(f"{first} {last}"))
    got_ln = last_norm(last)
    got_fi = fi_fn(first)
    ok = '✓' if got_ln == exp_ln and got_fi == exp_fi else '✗'
    print(f"{ok}  {first} {last}:  last_norm={got_ln!r} (exp {exp_ln!r})  fi={got_fi!r} (exp {exp_fi!r})")
```
