"""
find_committee_v.py — Compute spectral v-values and enclave metrics for ERA committee members.

Pipeline:
1. Ingest rosters:
   - ERA 2018 Research Evaluation Committees (RECs): 150 members/chairs across 8 disciplines
   - ERA EI Advisory Committee: 10 members from the national review report
2. Resolve committee members to OpenAlex author entities via name + institution matching.
3. Extract their authored works from flat_works and authorships.
4. Attach spectral prestige metrics:
   - Source prestige (v_s) from field rankings
   - Institutional prestige (mean_inst_v)
   - Blended work prestige (work_v = (v_s + mean_inst_v)/2)
   - Citer prestige (mean_citer_v = <v>) and citer quality ratio (hi vs lo)
   - Enclave quadrant classification (HCW++, HCW+-, HCW-+, HCW--)
   - Membership in named NMF topic enclaves
5. Aggregate to person-level metrics:
   - Unweighted per-work mean <<v_s>>, <<v_citer>>, <<v_work>>
   - Weighted pool mean <v_s>, <v_citer>
   - Enclave penetration (% HCW--, % HCW-+, etc.)
6. Save comprehensive output datasets (parquet + CSV) and print diagnostic report.

Usage:
  .venv/bin/python analysis/find_committee_v.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from nameparser import HumanName

# Set project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from util import load_config, load_settings, FIELD_NAMES
from util.name_util import clean_name, norm, last_norm, fi


def get_roster(data_dir: Path) -> pd.DataFrame:
    """Build unified committee roster from REC JSON and Advisory Committee records."""
    rec_file = data_dir / "era2018_rec_rosters.json"
    rec_data = json.loads(rec_file.read_text())

    records = []
    # 1. 2018 REC Rosters
    for r_code, r_obj in rec_data.items():
        comm_name = r_obj.get("committee_name", "")
        # Chair
        c = r_obj.get("chair")
        if c:
            hn = HumanName(clean_name(c["name"]))
            records.append({
                "panel": r_code,
                "panel_name": comm_name,
                "committee_type": "REC",
                "role": "Chair",
                "raw_name": c["name"],
                "clean_name": clean_name(c["name"]),
                "first_name": hn.first,
                "last_name": hn.last,
                "last_norm": last_norm(hn.last),
                "fi": fi(hn.first),
                "affil": c["affiliation"],
            })
        # Members
        for m in r_obj.get("members", []):
            hn = HumanName(clean_name(m["name"]))
            records.append({
                "panel": r_code,
                "panel_name": comm_name,
                "committee_type": "REC",
                "role": "Member",
                "raw_name": m["name"],
                "clean_name": clean_name(m["name"]),
                "first_name": hn.first,
                "last_name": hn.last,
                "last_norm": last_norm(hn.last),
                "fi": fi(hn.first),
                "affil": m["affiliation"],
            })

    # 2. ERA EI Advisory Committee (from ARC 2020-2021 review report)
    ac_members = [
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Chair", "Emeritus Professor Michael Brooks", "The University of Adelaide"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Professor Joy Damousi", "Australian Catholic University"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Dr Gemma Derrick", "University of Lancaster"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Dr Alan Finkel", "Monash University"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Dr Cathy Foley", "CSIRO"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Professor Anne Kelso", "The University of Melbourne"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Professor Emeritus Ian O’Connor", "Griffith University"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Emeritus Professor John O’Connor", "The University of Newcastle"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Dr Mathew Trinca", "National Museum of Australia"),
        ("ERA_EI_AC", "ERA EI Advisory Committee", "Advisory", "Member", "Professor Maggie Walter", "University of Tasmania"),
    ]

    for p, p_name, c_type, role, name, affil in ac_members:
        hn = HumanName(clean_name(name))
        records.append({
            "panel": p,
            "panel_name": p_name,
            "committee_type": c_type,
            "role": role,
            "raw_name": name,
            "clean_name": clean_name(name),
            "first_name": hn.first,
            "last_name": hn.last,
            "last_norm": last_norm(hn.last),
            "fi": fi(hn.first),
            "affil": affil,
        })

    df = pd.DataFrame(records)
    # Deduplicate in case any chair appears in multiple roles
    df["person_id"] = np.arange(len(df))
    return df


def match_affiliation_keyword(affil_str: str) -> str:
    """Extract standard institution search token from affiliation string."""
    s = affil_str.lower()
    tokens = [
        ("adelaide", "Adelaide"),
        ("australian national university", "Australian National"),
        ("anu", "Australian National"),
        ("canberra", "Canberra"),
        ("curtin", "Curtin"),
        ("deakin", "Deakin"),
        ("flinders", "Flinders"),
        ("griffith", "Griffith"),
        ("james cook", "James Cook"),
        ("la trobe", "La Trobe"),
        ("macquarie", "Macquarie"),
        ("melbourne", "Melbourne"),
        ("monash", "Monash"),
        ("murdoch", "Murdoch"),
        ("newcastle", "Newcastle"),
        ("new england", "New England"),
        ("new south wales", "New South Wales"),
        ("unsw", "New South Wales"),
        ("queensland university of technology", "Queensland University of Technology"),
        ("qut", "Queensland University of Technology"),
        ("queensland", "Queensland"),
        ("rmit", "RMIT"),
        ("south australia", "South Australia"),
        ("southern cross", "Southern Cross"),
        ("sunshine coast", "Sunshine Coast"),
        ("sydney", "Sydney"),
        ("tasmania", "Tasmania"),
        ("technology sydney", "University of Technology Sydney"),
        ("uts", "University of Technology Sydney"),
        ("western australia", "Western Australia"),
        ("uwa", "Western Australia"),
        ("western sydney", "Western Sydney"),
        ("wollongong", "Wollongong"),
        ("swinburne", "Swinburne"),
        ("charles darwin", "Charles Darwin"),
        ("cdu", "Charles Darwin"),
        ("edith cowan", "Edith Cowan"),
        ("catholic", "Catholic"),
        ("csiro", "CSIRO"),
        ("lancaster", "Lancaster"),
    ]
    for pattern, name in tokens:
        if pattern in s:
            return name
    return ""


def resolve_authors_duckdb(
    roster: pd.DataFrame,
    authors_glob: str,
) -> pd.DataFrame:
    """
    Match roster to OpenAlex author_idx using in-memory DuckDB.
    Scans authors once, matches on exact last_norm and first initial,
    then prioritises institutional matches and publication volume.
    """
    print(f"Resolving {len(roster)} committee members against OpenAlex authors...")
    t0 = time.time()
    con = duckdb.connect()

    # Register roster
    con.register("_roster", roster[["person_id", "raw_name", "first_name", "last_name", "last_norm", "fi", "affil"]])

    # Build candidates: exact match on last name and first initial
    print("  Scanning OpenAlex authors...")
    cands_query = f"""
        CREATE TEMP TABLE _cands AS
        SELECT
            r.person_id,
            r.raw_name,
            r.affil,
            a.author_idx,
            a.display_name,
            a.works_count,
            a.cited_by_count,
            a.h_index,
            a.orcid,
            a.last_known_institutions
        FROM parquet_scan('{authors_glob}') a
        JOIN _roster r
          ON LOWER(SPLIT_PART(TRIM(a.display_name), ' ', -1)) = r.last_norm
         AND LOWER(SUBSTR(TRIM(a.display_name), 1, 1)) = r.fi
        WHERE a.works_count > 0
    """
    con.execute(cands_query)
    n_cands = con.execute("SELECT COUNT(*) FROM _cands").fetchone()[0]
    print(f"  Extracted {n_cands:,} candidate records in {time.time()-t0:.1f}s")

    cands_df = con.execute("SELECT * FROM _cands").df()
    con.close()

    matched_rows = []
    # Disambiguate per person
    for pid, group in cands_df.groupby("person_id"):
        r_info = roster.loc[roster["person_id"] == pid].iloc[0]
        affil_kw = match_affiliation_keyword(r_info["affil"]).lower()
        target_first = norm(r_info["first_name"])

        # Check full first name match
        def first_match(dname):
            parts = [norm(p) for p in clean_name(dname).split()]
            return len(parts) > 1 and parts[0] == target_first

        group = group.copy()
        group["first_ok"] = group["display_name"].apply(first_match)

        # Check institution affiliation substring in last_known_institutions
        def inst_match(insts):
            if insts is None or len(insts) == 0 or not affil_kw:
                return False
            s = str(insts).lower()
            return affil_kw in s

        group["inst_ok"] = group["last_known_institutions"].apply(inst_match)

        # Priority hierarchy:
        # 1. first_ok AND inst_ok
        # 2. inst_ok
        # 3. first_ok
        # 4. highest cited_by_count
        p1 = group[group["first_ok"] & group["inst_ok"]]
        if not p1.empty:
            best = p1.sort_values("cited_by_count", ascending=False).iloc[0]
            status = "exact_inst"
        else:
            p2 = group[group["inst_ok"]]
            if not p2.empty:
                best = p2.sort_values("cited_by_count", ascending=False).iloc[0]
                status = "inst_only"
            else:
                p3 = group[group["first_ok"]]
                if not p3.empty:
                    best = p3.sort_values("cited_by_count", ascending=False).iloc[0]
                    status = "first_only"
                else:
                    best = group.sort_values("cited_by_count", ascending=False).iloc[0]
                    status = "fallback_highest_cite"

        matched_rows.append({
            "person_id": pid,
            "author_idx": best["author_idx"],
            "oa_name": best["display_name"],
            "oa_works_count": best["works_count"],
            "oa_cited_by": best["cited_by_count"],
            "oa_h_index": best["h_index"],
            "oa_orcid": best["orcid"],
            "match_status": status,
        })

    match_df = pd.DataFrame(matched_rows)
    resolved = roster.merge(match_df, on="person_id", how="left")
    print(f"  Successfully matched {resolved['author_idx'].notna().sum()} / {len(roster)} committee members")
    return resolved


def extract_and_label_works(
    resolved_members: pd.DataFrame,
    fw_path: str,
    authorships_glob: str,
    work_v_path: str,
    enclave_hcw_path: str,
    enclave_nmf_path: str,
    con: duckdb.DuckDBPyConnection,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Extract works for matched authors and attach:
    - work_v (source_v, mean_inst_v, work_v)
    - enclave_hcw (mean_citer_v, n_citer_hi, n_citer_lo, n_intra, quadrant)
    - enclave_nmf (enclave topic name)
    """
    print("\nExtracting authored works from OpenAlex authorships & flat_works...")
    t0 = time.time()

    valid_members = resolved_members.dropna(subset=["author_idx"]).copy()
    valid_members["author_idx"] = valid_members["author_idx"].astype(int)
    con.register("_valid_members", valid_members[["person_id", "author_idx", "panel", "role"]])

    # 1. Authored works scan
    con.execute(f"""
        CREATE TEMP TABLE _member_works AS
        SELECT DISTINCT
            m.person_id,
            m.author_idx,
            m.panel,
            m.role,
            a.work_idx
        FROM parquet_scan('{authorships_glob}') a
        JOIN _valid_members m ON a.author_idx = m.author_idx
        WHERE a.author_idx IS NOT NULL
    """)
    n_mw = con.execute("SELECT COUNT(*) FROM _member_works").fetchone()[0]
    print(f"  Found {n_mw:,} author-work links in {time.time()-t0:.1f}s")

    # 2. Join flat_works for metadata and field assignment
    t1 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _flat_member_works AS
        SELECT DISTINCT
            mw.person_id,
            mw.author_idx,
            mw.panel,
            mw.role,
            mw.work_idx,
            fw.publication_year,
            fw.source_idx,
            fw.institution_idx,
            fw.field_idx
        FROM _member_works mw
        JOIN parquet_scan('{fw_path}') fw ON fw.work_idx = mw.work_idx
        WHERE fw.field_idx BETWEEN 11 AND 36
    """)
    n_fmw = con.execute("SELECT COUNT(*) FROM _flat_member_works").fetchone()[0]
    print(f"  Joined flat_works: {n_fmw:,} rows in {time.time()-t1:.1f}s")

    # 3. Join work_v (source_v, mean_inst_v, work_v)
    t2 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _works_with_v AS
        SELECT
            fmw.*,
            wv.source_v,
            wv.mean_inst_v,
            wv.work_v
        FROM _flat_member_works fmw
        LEFT JOIN parquet_scan('{work_v_path}') wv
               ON wv.work_idx = fmw.work_idx
              AND wv.field_idx = fmw.field_idx
    """)

    # 4. Join enclave_hcw (mean_citer_v, n_citer_hi, n_citer_lo, n_intra, title)
    con.execute(f"""
        CREATE TEMP TABLE _works_with_hcw AS
        SELECT
            wv.*,
            eh.mean_citer_v,
            eh.n_intra,
            eh.n_citer_hi,
            eh.n_citer_lo,
            eh.title,
            CASE
                WHEN eh.work_idx IS NOT NULL AND wv.source_v >= 1.0 AND eh.mean_citer_v >= 1.0 THEN 'HCW++'
                WHEN eh.work_idx IS NOT NULL AND wv.source_v >= 1.0 AND eh.mean_citer_v < 1.0  THEN 'HCW+-'
                WHEN eh.work_idx IS NOT NULL AND wv.source_v < 1.0  AND eh.mean_citer_v >= 1.0 THEN 'HCW-+'
                WHEN eh.work_idx IS NOT NULL AND wv.source_v < 1.0  AND eh.mean_citer_v < 1.0  THEN 'HCW--'
                ELSE NULL
            END AS hcw_quadrant
        FROM _works_with_v wv
        LEFT JOIN parquet_scan('{enclave_hcw_path}') eh
               ON eh.work_idx = wv.work_idx
              AND eh.field_idx = wv.field_idx
    """)

    # 5. Join enclave_nmf (topic_name for HCW--)
    con.execute(f"""
        CREATE TEMP TABLE _full_works AS
        SELECT
            wh.*,
            en.topic_name AS enclave_name
        FROM _works_with_hcw wh
        LEFT JOIN parquet_scan('{enclave_nmf_path}') en
               ON en.work_idx = wh.work_idx
              AND en.field_idx = wh.field_idx
    """)
    print(f"  Attached v-scores, HCW quadrants, and enclaves in {time.time()-t2:.1f}s")

    # Pull full works table
    full_works_df = con.execute("SELECT * FROM _full_works").df()

    # 6. Aggregate person-level summary
    print("Computing member-level aggregate spectral profiles...")
    person_agg = con.execute("""
        SELECT
            person_id,
            COUNT(DISTINCT work_idx) AS n_works_corpus,
            COUNT(DISTINCT CASE WHEN source_v IS NOT NULL THEN work_idx END) AS n_ranked_works,
            
            -- Per-work unweighted averages <<v>>
            AVG(source_v)     AS mean_source_v,
            AVG(mean_inst_v)  AS mean_inst_v,
            AVG(work_v)       AS mean_work_v,
            AVG(mean_citer_v) AS mean_citer_v,
            
            -- Median v-values
            MEDIAN(source_v)     AS median_source_v,
            MEDIAN(work_v)       AS median_work_v,
            MEDIAN(mean_citer_v) AS median_citer_v,

            -- Citation quality totals
            SUM(n_citer_hi)   AS tot_citer_hi,
            SUM(n_citer_lo)   AS tot_citer_lo,
            CASE 
                WHEN (SUM(n_citer_hi) + SUM(n_citer_lo)) > 0 
                THEN CAST(SUM(n_citer_hi) AS DOUBLE) / (SUM(n_citer_hi) + SUM(n_citer_lo))
                ELSE NULL 
            END AS high_citer_ratio,

            -- Quadrant counts
            COUNT(DISTINCT CASE WHEN hcw_quadrant = 'HCW++' THEN work_idx END) AS n_hcw_pp,
            COUNT(DISTINCT CASE WHEN hcw_quadrant = 'HCW+-' THEN work_idx END) AS n_hcw_pm,
            COUNT(DISTINCT CASE WHEN hcw_quadrant = 'HCW-+' THEN work_idx END) AS n_hcw_mp,
            COUNT(DISTINCT CASE WHEN hcw_quadrant = 'HCW--' THEN work_idx END) AS n_hcw_mm,
            COUNT(DISTINCT CASE WHEN hcw_quadrant IS NOT NULL THEN work_idx END) AS n_hcw_total,
            
            -- Enclave works count
            COUNT(DISTINCT CASE WHEN enclave_name IS NOT NULL THEN work_idx END) AS n_enclave_works,
            STRING_AGG(DISTINCT enclave_name, '; ') AS enclave_topics
        FROM _full_works
        GROUP BY person_id
    """).df()

    summary_df = resolved_members.merge(person_agg, on="person_id", how="left")
    return full_works_df, summary_df


def main():
    paths = load_config()
    settings = load_settings()
    data_dir = paths.data
    working = paths.working

    fw_path = str(working / f"flat_works_{settings.year_min}_{settings.year_max}.parquet")
    authors_glob = f"{paths.openalex}/authors/*.parquet"
    authorships_glob = f"{paths.openalex}/authorships/*.parquet"
    work_v_path = str(working / "work_v_2020_2024_baseline.parquet")
    enclave_hcw_path = str(working / "enclave_hcw_2020_2024_baseline.parquet")
    enclave_nmf_path = str(working / "enclave_nmf_2020_2024_baseline.parquet")

    # Step 1: Build Roster
    roster = get_roster(data_dir)
    print(f"Roster loaded: {len(roster)} committee entries ({roster['panel'].nunique()} panels)")

    # Step 2: Resolve Authors
    resolved = resolve_authors_duckdb(roster, authors_glob)

    # Step 3 & 4: Extract and Label Works
    con = duckdb.connect()
    con.execute("SET memory_limit='16GB'; SET threads=8")
    works_df, summary_df = extract_and_label_works(
        resolved,
        fw_path,
        authorships_glob,
        work_v_path,
        enclave_hcw_path,
        enclave_nmf_path,
        con,
    )
    con.close()

    # Step 5: Save Datasets
    out_works = working / "era_committee_works_v.parquet"
    out_summary_pq = working / "era_committee_v_profiles.parquet"
    out_summary_csv = working / "era_committee_v_profiles.csv"

    works_df.to_parquet(out_works, index=False)
    summary_df.to_parquet(out_summary_pq, index=False)
    summary_df.to_csv(out_summary_csv, index=False)
    print(f"\nSaved works dataset -> {out_works}")
    print(f"Saved summary profiles -> {out_summary_pq} and {out_summary_csv}")

    # Step 6: Print Diagnostic Summary Table by Committee
    print("\n" + "=" * 90)
    print("ERA COMMITTEE SPECTRAL & ENCLAVE PROFILES SUMMARY")
    print("=" * 90)
    panel_summary = (
        summary_df.groupby("panel")
        .agg(
            n_members=("person_id", "count"),
            matched=("author_idx", lambda x: x.notna().sum()),
            med_works=("oa_works_count", "median"),
            med_cites=("oa_cited_by", "median"),
            med_h=("oa_h_index", "median"),
            mean_v_source=("mean_source_v", "mean"),
            mean_v_work=("mean_work_v", "mean"),
            mean_v_citer=("mean_citer_v", "mean"),
            citer_hi_ratio=("high_citer_ratio", "mean"),
            hcw_pp_total=("n_hcw_pp", "sum"),
            hcw_mm_total=("n_hcw_mm", "sum"),
            enclave_works_total=("n_enclave_works", "sum"),
        )
        .reset_index()
    )
    print(panel_summary.to_string(index=False))

    # Top standouts
    print("\nTop 10 Committee Members by Work Spectral Prestige (<<v_work>>):")
    top_v = summary_df.dropna(subset=["mean_work_v"]).sort_values("mean_work_v", ascending=False).head(10)
    for _, r in top_v.iterrows():
        print(f"  {r['clean_name']} ({r['panel']}, {r['affil']}): <<v_work>>={r['mean_work_v']:.3f}, <<v_citer>>={r['mean_citer_v']:.3f}, Works={r['n_works_corpus']}")

    print("\nCommittee Members with HCW-- / Enclave Participation:")
    enc_members = summary_df[summary_df["n_hcw_mm"] > 0].sort_values("n_hcw_mm", ascending=False)
    if not enc_members.empty:
        for _, r in enc_members.iterrows():
            print(f"  {r['clean_name']} ({r['panel']}, {r['affil']}): {r['n_hcw_mm']} HCW-- works | Enclaves: {r['enclave_topics']}")
    else:
        print("  None detected.")

    print("\nComplete!")


if __name__ == "__main__":
    main()
