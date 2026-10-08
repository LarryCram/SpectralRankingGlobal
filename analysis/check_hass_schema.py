"""
analysis/check_hass_schema.py — Inspect flat_works columns and subfield mappings for HASS analysis.
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

    print("--- flat_works schema ---")
    desc = db.execute(f"DESCRIBE SELECT * FROM '{fw_path}' LIMIT 1").df()
    print(desc[['column_name', 'column_type']])

    print("\n--- Testing FoR 2008 mapping for HASS ---")
    r = Resolver()
    # Check what FoR 2008 2-digit divisions exist
    res = r._con.execute("SELECT code, label FROM for_2008 WHERE length(code) = 2 ORDER BY code").fetchall()
    for code, label in res:
        print(f"  {code}: {label}")

if __name__ == '__main__':
    main()
