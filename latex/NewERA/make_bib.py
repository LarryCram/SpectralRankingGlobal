"""
make_bib.py — write references.bib holding only the entries cited by main.tex.

Reads the Zotero master library (../MyLibrary.bib, Better BibTeX or Better BibLaTeX export),
keeps the keys cited in main.aux, and maps BibLaTeX field names to the BibTeX names that
Frontiers-Harvard.bst reads (journaltitle -> journal, date -> year, location -> address,
institution -> school for theses). Frontiers asks for the .bib file to be uploaded with the
manuscript; this keeps it small.

Build:  pdflatex main && python make_bib.py && bibtex main && pdflatex main && pdflatex main
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER = HERE.parent / "MyLibrary.bib"
AUX = HERE / "main.aux"
OUT = HERE / "references.bib"


def cited_keys(aux: Path) -> set[str]:
    keys = set()
    for m in re.finditer(r"\\(?:citation|abx@aux@cite\{\d*\})\{([^}]*)\}", aux.read_text()):
        keys.update(k.strip() for k in m.group(1).split(","))
    return keys


def split_entries(text: str):
    """Yield (key, entry_text) for each @type{key, ...} entry, matching braces."""
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        depth, i = 0, text.index("{", m.start())
        for j in range(i, len(text)):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                yield m.group(2), m.group(1).lower(), text[m.start():j + 1]
                break


def to_bibtex(entry: str, etype: str) -> str:
    fields = set(re.findall(r"^\s{2}(\w+) = ", entry, flags=re.M))
    e = entry
    for f in ("file", "abstract"):
        e = drop_field(e, f)
    # BibLaTeX extended name format -> "prefix Family, Given"
    e = re.sub(r"family=([^,}]+), given=([^,}]+), prefix=([^,}]+), useprefix=(?:true|false)",
               r"\3 \1, \2", e)
    e = re.sub(r"family=([^,}]+), given=([^,}]+)", r"\1, \2", e)
    if "journal" not in fields:
        e = re.sub(r"^(\s{2})journaltitle = ", r"\1journal = ", e, flags=re.M)
    if "year" not in fields:
        e = re.sub(r"^(\s{2})date = \{(\d{4})[^}]*\}", r"\1year = {\2}", e, flags=re.M)
    if "address" not in fields:
        e = re.sub(r"^(\s{2})location = ", r"\1address = ", e, flags=re.M)
    if etype == "thesis":
        e = re.sub(r"^@thesis", "@phdthesis", e, flags=re.I)
        e = re.sub(r"^(\s{2})institution = ", r"\1school = ", e, flags=re.M)
    if etype == "report":
        e = re.sub(r"^@report", "@techreport", e, flags=re.I)
    if etype in ("legislation", "jurisdiction", "dataset", "software"):
        e = re.sub(r"^@\w+", "@misc", e)
    if etype == "online":
        e = re.sub(r"^@online", "@misc", e, flags=re.I)
        e = re.sub(r"^(\s{2})url = \{([^}]*)\}", r"\1howpublished = {\\url{\2}},\n\1url = {\2}", e, flags=re.M)
    return e


def drop_field(entry: str, name: str) -> str:
    """Remove `  name = {...},` with brace matching (abstracts can contain braces)."""
    m = re.search(rf"^\s{{2}}{name} = \{{", entry, flags=re.M)
    if not m:
        return entry
    depth = 0
    for j in range(m.end() - 1, len(entry)):
        depth += {"{": 1, "}": -1}.get(entry[j], 0)
        if depth == 0:
            end = j + 1
            if entry[end:end + 1] == ",":
                end += 1
            if entry[end:end + 1] == "\n":
                end += 1
            return entry[:m.start()] + entry[end:]
    return entry


def main():
    if not AUX.exists():
        sys.exit("main.aux not found: run pdflatex first")
    keys = cited_keys(AUX)
    master = MASTER.read_text(encoding="utf-8")
    found = {}
    for key, etype, entry in split_entries(master):
        if key in keys and key not in found:
            found[key] = to_bibtex(entry, etype)
    OUT.write_text("\n\n".join(found[k] for k in sorted(found)) + "\n", encoding="utf-8")
    missing = sorted(keys - set(found))
    print(f"references.bib: {len(found)} entries" + (f"; missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
