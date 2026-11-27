"""
load_stanford.py — R4: convert the Stanford/Elsevier top-2% database (v9, August 2026
data update) from the distributed .xlsx files to parquet.

Input : data/UNTRACKED/August 2026 data-update for Updated science-wide a.zip
Output: WORKING/researchers/stanford/
          stanford_career.parquet, stanford_singleyr.parquet   (Table 1, Data sheet)
          stanford_field_thresholds_{career,singleyr}.parquet  (Table 2, Field sheet)
          stanford_subfield_thresholds_{career,singleyr}.parquet (Table 2, SubField sheet)

Column names are normalised: lower case, ' (ns)' -> '_ns', the year-stamped names
are made generic (np6025 -> np, nc9625/nc2525 -> nc, h25 -> h, hm25 -> hm,
'np6025 cited9625' -> np_cited, *_rw retraction columns keep their stem), and any
other non-alphanumeric run becomes '_'. A row id `sid` keeps the file order.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researchers.common import ROOT, add_path_args, resolve_paths
from util import guard

ZIP = ROOT / 'data' / 'UNTRACKED' / 'August 2026 data-update for Updated science-wide a.zip'


def norm_col(c: str) -> str:
    c = str(c).strip()
    ns = c.endswith('(ns)')
    c = c.replace('(ns)', '').strip().lower()
    c = re.sub(r'np6025 cited\d{4}', 'np_cited', c)
    c = re.sub(r'^nc\d{4}', 'nc', c)
    c = re.sub(r'^np6025', 'np', c)
    c = re.sub(r'^hm\d{2}$', 'hm', c)
    c = re.sub(r'^h\d{2}$', 'h', c)
    c = re.sub(r'[^a-z0-9%]+', '_', c).strip('_').replace('%', '_pct')
    return c + ('_ns' if ns else '')


def read_sheet(data: bytes, sheet: str) -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = wb[sheet].iter_rows(values_only=True)
    header = [norm_col(h) for h in next(rows)]
    df = pd.DataFrame(list(rows), columns=header)
    wb.close()
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    add_path_args(ap)
    args = ap.parse_args()
    p = resolve_paths(args)
    out = p.stanford_dir
    target = out / 'stanford_career.parquet'
    if not guard.ensure_fresh(target, ZIP, script=__file__, auto_yes=args.yes,
                              label='stanford'):
        return
    t0 = time.time()
    z = zipfile.ZipFile(ZIP)
    names = {Path(n).name: n for n in z.namelist()}
    for kind in ('career', 'singleyr'):
        t1 = time.time()
        t1n = next(n for n in names if n.startswith('Table_1_') and f'_{kind}_' in n)
        df = read_sheet(z.read(names[t1n]), 'Data')
        df.insert(0, 'sid', range(len(df)))
        df.to_parquet(out / f'stanford_{kind}.parquet', index=False)
        print(f'  {kind}: {len(df):,} rows, {df.shape[1]} cols  [{time.time()-t1:.0f}s]')
        t2n = next(n for n in names if n.startswith('Table_2_') and f'_{kind}_' in n)
        data = z.read(names[t2n])
        read_sheet(data, 'Field').to_parquet(out / f'stanford_field_thresholds_{kind}.parquet', index=False)
        read_sheet(data, 'SubField').to_parquet(out / f'stanford_subfield_thresholds_{kind}.parquet', index=False)
    print(f'Total [{time.time()-t0:.0f}s]')
    guard.record_build(target, ZIP, script=__file__, build_seconds=time.time() - t0)


if __name__ == '__main__':
    main()
