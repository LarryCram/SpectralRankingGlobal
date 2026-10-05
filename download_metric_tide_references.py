#!/usr/bin/env python3
"""
download_metric_tide_references.py

Downloads all works citing "The Metric Tide" (and its Literature Review) 
from OpenAlex published from 2016 onwards.

Target OpenAlex Entities:
- W2163887529 (The Metric Tide: Report of the Independent Review...)
- W2588841625 (The Metric Tide: Independent Review... SAGE)
- W2757973397 (The metric tide: Literature review)

Outputs:
- data/metric_tide/metric_tide_referencing_works.json
- data/metric_tide/metric_tide_referencing_works.csv
- data/metric_tide/metric_tide_referencing_works.bib
- data/metric_tide/METRIC_TIDE_REFERENCING_CATALOGUE.md
"""

import os
import sys
import json
import csv
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "metric_tide")
os.makedirs(OUTPUT_DIR, exist_ok=True)

TARGET_WORKS = ["W2163887529", "W2588841625", "W2757973397"]
FROM_DATE = "2016-01-01"

HEADERS = {
    "User-Agent": "AntigravityResearch/1.0 (mailto:academic-research@universities.edu.au)"
}

def reconstruct_abstract(inverted_index):
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    word_pos = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_pos.append((pos, word))
    word_pos.sort(key=lambda x: x[0])
    return " ".join([w[1] for w in word_pos])

def fetch_all_citing_works():
    cites_param = "|".join(TARGET_WORKS)
    base_url = "https://api.openalex.org/works"
    cursor = "*"
    per_page = 200
    
    all_works = []
    page_num = 1
    
    print(f"[*] Starting download from OpenAlex for citing works (from {FROM_DATE})...")
    
    while cursor:
        params = {
            "filter": f"cites:{cites_param},from_publication_date:{FROM_DATE}",
            "per_page": per_page,
            "cursor": cursor
        }
        encoded_url = f"{base_url}?{urllib.parse.urlencode(params)}"
        
        req = urllib.request.Request(encoded_url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[!] Error fetching page {page_num}: {e}. Retrying in 2 seconds...")
            time.sleep(2)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e2:
                print(f"[X] Failed page {page_num} on retry: {e2}. Stopping pagination.")
                break
                
        results = data.get("results", [])
        if not results:
            break
            
        all_works.extend(results)
        meta = data.get("meta", {})
        total_count = meta.get("count", 0)
        next_cursor = meta.get("next_cursor")
        
        print(f"  -> Page {page_num}: Retrieved {len(results)} works (Progress: {len(all_works)} / {total_count})")
        
        if not next_cursor or next_cursor == cursor or len(results) < per_page:
            break
            
        cursor = next_cursor
        page_num += 1
        time.sleep(0.1)  # Respect OpenAlex rate etiquette
        
    print(f"[+] Download complete: Total {len(all_works)} citing works retrieved.")
    return all_works

def parse_work_record(w):
    openalex_id = w.get("id", "")
    doi = w.get("doi", "") or ""
    title = w.get("title") or "Untitled"
    year = w.get("publication_year")
    pub_date = w.get("publication_date") or ""
    work_type = w.get("type", "article")
    cited_by_count = w.get("cited_by_count", 0)
    
    # Authors
    authors_list = []
    affiliations_list = []
    for authorship in w.get("authorships", []):
        author = authorship.get("author", {})
        author_name = author.get("display_name")
        if author_name:
            authors_list.append(author_name)
        for inst in authorship.get("institutions", []):
            inst_name = inst.get("display_name")
            if inst_name and inst_name not in affiliations_list:
                affiliations_list.append(inst_name)
                
    first_author = authors_list[0] if authors_list else "Unknown"
    authors_str = "; ".join(authors_list)
    institutions_str = "; ".join(affiliations_list)
    
    # Source / Venue
    primary_location = w.get("primary_location") or {}
    source = primary_location.get("source") or {}
    source_name = source.get("display_name") or ""
    publisher = source.get("publisher") or ""
    issn_l = source.get("issn_l") or ""
    
    # Open Access
    oa_info = w.get("open_access") or {}
    is_oa = oa_info.get("is_oa", False)
    oa_status = oa_info.get("oa_status", "closed")
    oa_url = oa_info.get("oa_url") or primary_location.get("landing_page_url") or ""
    
    # Topic
    primary_topic = w.get("primary_topic") or {}
    topic_name = primary_topic.get("display_name") or ""
    subfield_name = (primary_topic.get("subfield") or {}).get("display_name") or ""
    field_name = (primary_topic.get("field") or {}).get("display_name") or ""
    domain_name = (primary_topic.get("domain") or {}).get("display_name") or ""
    
    # Abstract
    abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
    
    return {
        "openalex_id": openalex_id,
        "doi": doi,
        "title": title,
        "year": year,
        "publication_date": pub_date,
        "authors": authors_str,
        "first_author": first_author,
        "author_count": len(authors_list),
        "institutions": institutions_str,
        "venue": source_name,
        "publisher": publisher,
        "issn_l": issn_l,
        "work_type": work_type,
        "cited_by_count": cited_by_count,
        "is_oa": is_oa,
        "oa_status": oa_status,
        "oa_url": oa_url,
        "topic": topic_name,
        "subfield": subfield_name,
        "field": field_name,
        "domain": domain_name,
        "abstract": abstract
    }

def clean_bibtex_key(author, year, title):
    surname = author.split()[-1] if author else "Unknown"
    surname = re.sub(r"[^a-zA-Z]", "", surname).lower()
    first_word = re.sub(r"[^a-zA-Z]", "", title.split()[0]).lower() if title else "paper"
    year_str = str(year) if year else "nodate"
    return f"{surname}{year_str}{first_word}"

def generate_bibtex_entry(rec):
    key = clean_bibtex_key(rec["first_author"], rec["year"], rec["title"])
    # clean authors for bibtex (separated by " and ")
    authors_bib = rec["authors"].replace("; ", " and ") if rec["authors"] else "Unknown"
    title_escaped = rec["title"].replace("{", "").replace("}", "")
    
    entry_type = "article"
    if rec["work_type"] in ["book", "monograph"]:
        entry_type = "book"
    elif rec["work_type"] in ["book-chapter", "proceedings-article"]:
        entry_type = "inproceedings"
    elif rec["work_type"] in ["report", "working-paper"]:
        entry_type = "techreport"
        
    lines = [f"@{entry_type}{{{key},"]
    lines.append(f"  author    = {{{authors_bib}}},")
    lines.append(f"  title     = {{{{{title_escaped}}}}},")
    if rec["venue"]:
        lines.append(f"  journal   = {{{rec['venue']}}},")
    if rec["year"]:
        lines.append(f"  year      = {{{rec['year']}}},")
    if rec["doi"]:
        doi_clean = rec["doi"].replace("https://doi.org/", "")
        lines.append(f"  doi       = {{{doi_clean}}},")
    if rec["oa_url"]:
        lines.append(f"  url       = {{{rec['oa_url']}}},")
    lines.append("}\n")
    return "\n".join(lines)

def export_data(parsed_records):
    # 1. JSON
    json_path = os.path.join(OUTPUT_DIR, "metric_tide_referencing_works.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(parsed_records, f, indent=2, ensure_ascii=False)
    print(f"[✓] Saved JSON: {json_path}")
    
    # 2. CSV
    csv_path = os.path.join(OUTPUT_DIR, "metric_tide_referencing_works.csv")
    fieldnames = [
        "openalex_id", "doi", "title", "year", "publication_date",
        "first_author", "authors", "author_count", "institutions",
        "venue", "publisher", "work_type", "cited_by_count",
        "is_oa", "oa_status", "oa_url", "topic", "subfield", "field", "domain",
        "abstract"
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in parsed_records:
            writer.writerow(r)
    print(f"[✓] Saved CSV: {csv_path}")
    
    # 3. BibTeX
    bib_path = os.path.join(OUTPUT_DIR, "metric_tide_referencing_works.bib")
    with open(bib_path, "w", encoding="utf-8") as f:
        for r in parsed_records:
            f.write(generate_bibtex_entry(r))
            f.write("\n")
    print(f"[✓] Saved BibTeX: {bib_path}")
    
    # 4. Comprehensive Markdown Catalogue
    md_path = os.path.join(OUTPUT_DIR, "METRIC_TIDE_REFERENCING_CATALOGUE.md")
    
    # Sort by citation count descending
    sorted_by_cites = sorted(parsed_records, key=lambda x: x["cited_by_count"], reverse=True)
    
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Catalogue of Publications Referencing *The Metric Tide* (2016–2026)\n\n")
        f.write(f"**Data Source:** OpenAlex API  \n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
        f.write(f"**Total Works Indexing Citations to Metric Tide:** {len(parsed_records)}  \n")
        f.write(f"**Target Reports:** W2163887529 (Report), W2588841625 (SAGE Book), W2757973397 (Literature Review)\n\n")
        
        f.write("### Data Export Files\n")
        f.write("- **CSV Spreadsheet:** [`metric_tide_referencing_works.csv`](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/data/metric_tide/metric_tide_referencing_works.csv)\n")
        f.write("- **Full JSON Dataset:** [`metric_tide_referencing_works.json`](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/data/metric_tide/metric_tide_referencing_works.json)\n")
        f.write("- **BibTeX Citations:** [`metric_tide_referencing_works.bib`](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/data/metric_tide/metric_tide_referencing_works.bib)\n\n")
        
        f.write("## 1. Top 50 Most-Cited Referencing Publications\n\n")
        f.write("| # | Year | Citations | Title | First Author | Venue | Links |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for idx, r in enumerate(sorted_by_cites[:50], 1):
            doi_link = f"[DOI]({r['doi']})" if r['doi'] else "No DOI"
            oa_link = f"[OpenAlex]({r['openalex_id']})"
            title_escaped = r['title'].replace("|", "\\|")
            venue_escaped = (r['venue'] or 'N/A').replace("|", "\\|")
            f.write(f"| {idx} | {r['year']} | {r['cited_by_count']} | {title_escaped} | {r['first_author']} | {venue_escaped} | {doi_link} · {oa_link} |\n")
            
        f.write("\n\n## 2. Breakdown by Primary Subject Domain\n\n")
        domain_counts = {}
        for r in parsed_records:
            dom = r["domain"] or "Uncategorized"
            domain_counts[dom] = domain_counts.get(dom, 0) + 1
            
        for dom, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / len(parsed_records)) * 100
            f.write(f"- **{dom}:** {count} publications ({pct:.1f}%)\n")
            
        f.write("\n\n## 3. Chronological Inventory of Key Studies (2016–2026)\n\n")
        
        # Group by year
        by_year = {}
        for r in parsed_records:
            yr = r["year"] or "Unknown"
            by_year.setdefault(yr, []).append(r)
            
        for yr in sorted(by_year.keys(), reverse=True):
            works_yr = by_year[yr]
            f.write(f"### {yr} ({len(works_yr)} publications)\n\n")
            for w in sorted(works_yr, key=lambda x: x["cited_by_count"], reverse=True)[:15]:
                f.write(f"- **{w['title']}**  \n")
                f.write(f"  *Authors:* {w['authors'][:150]}{'...' if len(w['authors']) > 150 else ''}  \n")
                f.write(f"  *Venue:* {w['venue'] or 'N/A'} ({w['work_type']}) | *Citations:* {w['cited_by_count']}  \n")
                if w['doi']:
                    f.write(f"  *DOI:* [{w['doi']}]({w['doi']}) | *OpenAlex:* [{w['openalex_id']}]({w['openalex_id']})  \n")
                else:
                    f.write(f"  *OpenAlex:* [{w['openalex_id']}]({w['openalex_id']})  \n")
                if w['topic']:
                    f.write(f"  *Topic:* {w['topic']} ({w['field']})  \n")
                f.write("\n")
                
    print(f"[✓] Saved Markdown Catalogue: {md_path}")

def main():
    start_time = time.time()
    raw_works = fetch_all_citing_works()
    if not raw_works:
        print("[!] No records fetched.")
        return
        
    print(f"[*] Parsing {len(raw_works)} work records...")
    parsed_records = [parse_work_record(w) for w in raw_works]
    
    export_data(parsed_records)
    elapsed = time.time() - start_time
    print(f"[★] All tasks completed successfully in {elapsed:.1f}s.")

if __name__ == "__main__":
    main()
