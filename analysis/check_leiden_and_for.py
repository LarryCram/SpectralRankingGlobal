"""
analysis/check_leiden_and_for.py — Check leiden_name values and FoR 2008 mapping for HASS.
"""

from pathlib import Path
import sys
import duckdb

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
from util import load_config
from research_classification import Resolver

def main():
    paths = load_config()
    fw_path = paths.working / "flat_works_2000_2025.parquet"
    db = duckdb.connect()

    print("--- Distinct leiden_idx and leiden_name in flat_works ---")
    df_l = db.execute(f"""
        SELECT leiden_idx, leiden_name, count(*) as count
        FROM '{fw_path}'
        WHERE publication_year = 2022
        GROUP BY leiden_idx, leiden_name
        ORDER BY leiden_idx
    """).df()
    print(df_l)

    print("\n--- FoR 2008 subfield mapping ---")
    r = Resolver()
    subfields = db.execute(f"SELECT DISTINCT subfield_idx FROM '{fw_path}' WHERE subfield_idx IS NOT NULL").df()['subfield_idx'].tolist()
    div_counts = {}
    for sf in subfields:
        res = r.resolve(str(sf), "OAX", "FOR2008")
        div = res.code[:2]
        div_counts[div] = div_counts.get(div, 0) + 1
    print("Subfields per 2-digit FoR division:")
    for k in sorted(div_counts.keys()):
        print(f"  Div {k}: {div_counts[k]} subfields")

if __name__ == '__main__':
    main()
