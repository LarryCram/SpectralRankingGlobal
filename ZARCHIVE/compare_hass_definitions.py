"""
analysis/compare_hass_definitions.py — Compare leiden_idx == 5 vs FoR divisions 12-22.
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

    r = Resolver()
    # Map subfields to FoR 2008 division
    subfield_df = db.execute(f"SELECT DISTINCT subfield_idx, leiden_idx, leiden_name FROM '{fw_path}' WHERE subfield_idx IS NOT NULL").df()
    
    subfield_df['for_div'] = subfield_df['subfield_idx'].apply(lambda sf: r.resolve(str(sf), "OAX", "FOR2008").code[:2])
    subfield_df['is_for_hass'] = subfield_df['for_div'].astype(int) >= 12
    subfield_df['is_leiden_ssh'] = subfield_df['leiden_idx'] == 5

    ct = db.execute("""
        SELECT for_div, is_for_hass, leiden_idx, leiden_name, count(*) as n_subfields
        FROM subfield_df
        GROUP BY for_div, is_for_hass, leiden_idx, leiden_name
        ORDER BY for_div
    """).df()
    print("Cross-tab of FoR division vs Leiden field:")
    print(ct.to_string())

if __name__ == '__main__':
    main()
