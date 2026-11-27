"""
era2018_analysis.py — ERA 2018 testbed statistics, tables and figure for the NewERA paper.

Joins the ERA 2018 emulation (pipeline/run_era2018.py, HEP report) to the official ERA 2018
ratings and the ERA 2018 Discipline Matrix, then writes every number quoted in the
Results section to tables/ (LaTeX), figures/ (PDF) and era2018_numbers.json.

Evaluation mode per FoR code comes from the ERA 2018 Discipline Matrix
(data/era2018_discipline_matrix.csv, parsed from the ARC PDF): citation analysis ("CA"),
peer review ("PR"), or mixed at the two-digit level ("MIX": 01 and 10, where the
four-digit groups use different modes).

Usage:
    .venv/bin/python latex/NewERA/era2018_analysis.py [--working DIR] [--outcomes DIR]
"""

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from util import load_config  # noqa: E402

DEFAULT_OUTCOMES = Path("/home/lc/Dropbox/RESEARCH/ERA/data/ERA_OUTCOMES")
N_BOOT = 2000
RNG = np.random.default_rng(20181)

MODE_NAME = {"CA": "Citation", "PR": "Peer review", "MIX": "Mixed"}

# Evaluating committee (REC) per two-digit division, from the Discipline Matrix
# (division 10 is assessed jointly by BB, EE and MHS; EE is used here).
FOR_TO_REC = {
    "01": "MIC", "02": "PCE", "03": "PCE", "04": "PCE", "05": "EE", "06": "BB",
    "07": "BB", "08": "MIC", "09": "EE", "10": "EE", "11": "MHS", "12": "HCA",
    "13": "EHS", "14": "EC", "15": "EC", "16": "EHS", "17": "MHS", "18": "HCA",
    "19": "HCA", "20": "HCA", "21": "HCA", "22": "HCA",
}


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────
def load_matrix() -> pd.DataFrame:
    mx = pd.read_csv(ROOT / "data/era2018_discipline_matrix.csv", dtype=str)
    mx["mode"] = np.select([mx.citation == "-", mx.citation == "Yes"], ["PR", "CA"], "MIX")
    return mx


def load_outcomes(outcomes_dir: Path) -> pd.DataFrame:
    frames = [pd.read_pickle(f) for f in sorted(glob.glob(str(outcomes_dir / "*.pkl")))]
    o = pd.concat(frames, ignore_index=True)
    code = o["FOR_code"].astype(str)
    o["code"] = np.where(code.str.len() <= 2, code.str.zfill(2), code.str.zfill(4))
    o = o[o[2018].astype(str).isin(list("12345"))].copy()
    o["era"] = o[2018].astype(int)
    return o.rename(columns={"short_name": "hep"})[["hep", "code", "era"]]


def load_emulation(working: Path) -> pd.DataFrame:
    h = pd.read_parquet(working / "era2018/hep_reports/era2018_au_hep_spectral_rankings.parquet")
    return h.rename(columns={"hep_short_name": "hep", "for_code": "code"})[
        ["hep", "code", "for_level", "v", "a_p", "rank_v_global"]]


def load_diags(working: Path) -> dict:
    out = {}
    for lvl, prefix in (("division", "div"), ("group", "grp")):
        for f in (working / "era2018" / lvl).glob(f"rankings_{prefix}_*_2011_2016_baseline_diag.json"):
            d = json.loads(f.read_text())
            out[d["for_code"]] = d
    return out


def load_committee(data_dir: Path) -> dict:
    """Count REC members (chair included) per (REC, HEP) using HEP_concordances names."""
    rec = json.loads((data_dir / "era2018_rec_rosters.json").read_text())
    keys = pd.read_excel(data_dir / "HEP_concordances.xlsx", sheet_name="Keys")
    variants = pd.read_excel(data_dir / "HEP_concordances.xlsx", sheet_name="Variants")
    return rec, keys, variants


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────
def spearman_ci(x, y, n_boot=N_BOOT):
    x, y = np.asarray(x), np.asarray(y)
    rho = spearmanr(x, y)[0]
    boots = []
    n = len(x)
    for _ in range(n_boot):
        i = RNG.integers(0, n, n)
        if np.unique(y[i]).size > 1:
            boots.append(spearmanr(x[i], y[i])[0])
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    return rho, lo, hi


def ols_cluster(df: pd.DataFrame, cols: list[str], fe: str, cluster: str) -> dict:
    """OLS of era on cols + fixed effects, cluster-robust (CR1) standard errors."""
    X = pd.concat([df[cols].astype(float),
                   pd.get_dummies(df[fe], dtype=float)], axis=1).to_numpy()
    y = df["era"].to_numpy(float)
    XtX_inv = np.linalg.pinv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    meat = np.zeros((X.shape[1], X.shape[1]))
    for idx in df.reset_index(drop=True).groupby(cluster).indices.values():
        s = X[idx].T @ e[idx]
        meat += np.outer(s, s)
    g, (n, k) = df[cluster].nunique(), X.shape
    V = (g / (g - 1)) * ((n - 1) / (n - k)) * XtX_inv @ meat @ XtX_inv
    se = np.sqrt(np.diag(V))
    return {c: {"b": float(b[i]), "se": float(se[i]),
                "p": float(2 * norm.sf(abs(b[i] / se[i])))} for i, c in enumerate(cols)} | {
        "n": int(n), "clusters": int(g)}


# ─────────────────────────────────────────────────────────────────────────────
# LaTeX helpers
# ─────────────────────────────────────────────────────────────────────────────
def f2(x):
    return "--" if pd.isna(x) else f"{x:.2f}"


def pct(g) -> str:
    return "--" if len(g) == 0 else f"{100 * (g.v >= 1).mean():.0f}"


def title_case(s: str) -> str:
    return s.replace("&", r"\&")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--working", type=Path, default=None,
                    help="WORKING dir holding era2018/ (default: config.yaml WORKING)")
    ap.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOMES)
    ap.add_argument("--compare-working", type=Path, default=None,
                    help="second WORKING dir (another OpenAlex snapshot) for a stability check")
    args = ap.parse_args()

    paths = load_config()
    working = args.working or paths.working
    tables, figures = HERE / "tables", HERE / "figures"
    tables.mkdir(exist_ok=True)
    figures.mkdir(exist_ok=True)

    mx = load_matrix()
    mode = dict(zip(mx.code, mx["mode"]))
    titles = dict(zip(mx.code, mx.title))
    o = load_outcomes(args.outcomes)
    h = load_emulation(working)
    diags = load_diags(working)

    m = o.merge(h, on=["hep", "code"], how="left")
    m["mode"] = m.code.map(mode)
    m["level"] = np.where(m.code.str.len() == 2, "division", "group")
    m["log_v"] = np.log10(m.v)
    ev = m.dropna(subset=["v"]).copy()

    keys = pd.read_excel(paths.data / "HEP_concordances.xlsx", sheet_name="Keys")
    go8 = set(keys.loc[keys["Group"].astype(str).str.upper().eq("GO8"), "HEP"])
    ev["is_go8"] = ev.hep.isin(go8).astype(int)

    num = {"working": str(working), "n_rated": {}, "n_matched": {}}
    for lvl in ("division", "group"):
        num["n_rated"][lvl] = int((m.level == lvl).sum())
        num["n_matched"][lvl] = int(((m.level == lvl) & m.v.notna()).sum())
    num["n_hep_rated"] = int(o.hep.nunique())
    d2 = m[m.level == "division"]
    num["division_rating_dist"] = {
        "matched": {int(k): int(v) for k, v in d2[d2.v.notna()].era.value_counts().sort_index().items()},
        "unmatched": {int(k): int(v) for k, v in d2[d2.v.isna()].era.value_counts().sort_index().items()},
        "mean_matched": float(d2[d2.v.notna()].era.mean()),
        "mean_unmatched": float(d2[d2.v.isna()].era.mean()),
    }
    num["coverage_by_level_mode"] = {
        f"{lvl}|{md}": [int(((m.level == lvl) & (m["mode"] == md) & m.v.notna()).sum()),
                        int(((m.level == lvl) & (m["mode"] == md)).sum())]
        for lvl in ("division", "group") for md in ("CA", "PR", "MIX")}
    num["n_go8"] = len(go8)

    # ── Table: division-level agreement ─────────────────────────────────────
    div = ev[ev.level == "division"]
    rows, recs = [], []
    for code in sorted(m.loc[m.level == "division", "code"].unique()):
        g = div[div.code == code]
        n_rated = int(((m.code == code)).sum())
        d = diags.get(code, {})
        rho = lo = hi = np.nan
        if len(g) >= 5 and g.era.nunique() > 1:
            rho, lo, hi = spearman_ci(g.v, g.era)
        recs.append(dict(code=code, title=titles[code], mode=mode[code], n_rated=n_rated,
                         n_matched=len(g), n_s=d.get("n_s_ranked"), n_i=d.get("n_u_ranked"),
                         gap=d.get("spectral_gap"), rho=rho, lo=lo, hi=hi,
                         share_ge1=float((g.v >= 1).mean()) if len(g) else np.nan))
        ci = "" if pd.isna(rho) else f"[{lo:.2f}, {hi:.2f}]"
        rows.append(f"{code} & {title_case(titles[code])} & {MODE_NAME[mode[code]]} & "
                    f"{n_rated} & {len(g)} & {d.get('n_s_ranked', 0):,} & {d.get('n_u_ranked', 0):,} & "
                    f"{f2(d.get('spectral_gap'))} & {pct(g)} & {f2(rho)} & {ci} \\\\")
    pd.DataFrame(recs).to_csv(tables / "era2018_division_agreement.csv", index=False)
    num["division"] = recs

    (tables / "tab_era2018_divisions.tex").write_text(r"""\begin{table}[htbp]
\centering
\scriptsize
\caption{ERA 2018 testbed by two-digit FoR 2008 division. Mode is the ERA 2018 evaluation method
from the Discipline Matrix (mixed: four-digit groups within the division use different methods).
Rated: units of evaluation (UoEs) rated 1--5 in ERA 2018. Matched: rated UoEs whose institution
is retained in the emulated division. $N_S$, $N_I$: sources and institutions (worldwide) in the
retained network. Gap: spectral gap $1-|\lambda_2|$. $v \ge 1$: percentage of matched UoEs at or
above the world mean. $r_s$: Spearman correlation between $v$ and the ERA rating over matched
UoEs, with bootstrap 95\% interval.}
\label{tab:era_divisions}
\resizebox{\textwidth}{!}{%
\begin{tabular}{llllrrrrrrl}
\toprule
FoR & Division & Mode & Rated & Matched & $N_S$ & $N_I$ & Gap & $v \ge 1$ (\%) & $r_s$ & 95\% CI \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}}
\end{table}
""")

    # ── Pooled agreement by level x mode ────────────────────────────────────
    pooled = []
    for lvl in ("division", "group"):
        for md in ("CA", "PR", "MIX"):
            g = ev[(ev.level == lvl) & (ev["mode"] == md)]
            if len(g) < 5:
                continue
            # within-code ranks remove between-discipline differences in rating level
            rv = g.groupby("code").v.rank(pct=True)
            re_ = g.groupby("code").era.rank(pct=True)
            rho_w = spearmanr(rv, re_)[0]
            pooled.append(dict(level=lvl, mode=md, n=len(g), codes=int(g.code.nunique()),
                               rho_within=float(rho_w),
                               rated=int(((m.level == lvl) & (m["mode"] == md)).sum())))
    num["pooled"] = pooled

    # ── Rating distribution by v band (divisions) ───────────────────────────
    div_bands = div.assign(band=np.where(div.v >= 1, "v>=1", "v<1"))
    xt = pd.crosstab([div_bands["mode"], div_bands.band], div_bands.era)
    num["rating_by_band"] = {f"{k[0]}|{k[1]}": {int(c): int(xt.loc[k, c]) for c in xt.columns}
                             for k in xt.index}
    num["share_rated_ge4"] = {f"{k[0]}|{k[1]}": float((xt.loc[k, [c for c in xt.columns if c >= 4]].sum())
                                                       / xt.loc[k].sum()) for k in xt.index}

    # ── Institutional prestige premium (divisions) ──────────────────────────
    reg = {}
    rec, _, _ = load_committee(paths.data)
    has_rec = committee_flags(rec, keys)
    div = div.assign(is_go8=div.hep.isin(go8).astype(int),
                     has_rec=[int((FOR_TO_REC[c], hp) in has_rec) for c, hp in zip(div.code, div.hep)])
    for md, g in (("ALL", div), ("CA", div[div["mode"] == "CA"]), ("PR", div[div["mode"] == "PR"])):
        g = g.reset_index(drop=True)
        reg[md] = {
            "v_only": ols_cluster(g, ["log_v"], "code", "hep"),
            "go8": ols_cluster(g, ["log_v", "is_go8"], "code", "hep"),
            "rec": ols_cluster(g, ["log_v", "has_rec"], "code", "hep"),
            "go8_rec": ols_cluster(g, ["log_v", "is_go8", "has_rec"], "code", "hep"),
        }
    num["regression"] = reg
    num["share_has_rec"] = float(div.has_rec.mean())

    # Sensitivity: drop divisions whose network is too sparse to rank
    # (spectral gap < 0.1 or fewer than ten matched UoEs).
    sparse = sorted(c for c, r in ((r["code"], r) for r in recs)
                    if (r["gap"] or 0) < 0.1 or r["n_matched"] < 10)
    num["sparse_divisions"] = sparse
    dense = div[~div.code.isin(sparse)]
    num["regression_dense"] = {
        md: ols_cluster(g.reset_index(drop=True), ["log_v", "is_go8", "has_rec"], "code", "hep")
        for md, g in (("ALL", dense), ("CA", dense[dense["mode"] == "CA"]),
                      ("PR", dense[dense["mode"] == "PR"]))}
    num["pooled_dense_PR"] = float(spearmanr(
        dense[dense["mode"] == "PR"].groupby("code").v.rank(pct=True),
        dense[dense["mode"] == "PR"].groupby("code").era.rank(pct=True))[0])

    rrows = []
    for md in ("CA", "PR"):
        r1, r2 = reg[md]["go8"], reg[md]["go8_rec"]
        rrows.append(
            f"{MODE_NAME[md]} & {r1['n']} & {r1['log_v']['b']:.2f} ({r1['log_v']['se']:.2f}) & "
            f"{r1['is_go8']['b']:.2f} ({r1['is_go8']['se']:.2f}) & "
            f"{r2['has_rec']['b']:.2f} ({r2['has_rec']['se']:.2f}) \\\\")
    (tables / "tab_era2018_premium.tex").write_text(r"""\begin{table}[htbp]
\centering
\small
\caption{Linear models of the ERA 2018 rating of two-digit UoEs on $\log_{10} v$, with division
fixed effects. Standard errors (in parentheses) are clustered by institution. Go8: membership of
the Group of Eight. REC: the institution had a member on the Research Evaluation Committee that
rated the division (coefficient from the model that also includes Go8).}
\label{tab:era_premium}
\begin{tabular}{lrrrr}
\toprule
Mode & UoEs & $\log_{10} v$ & Go8 & REC member \\
\midrule
""" + "\n".join(rrows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

    if args.compare_working:
        other = load_emulation(args.compare_working)
        num["snapshot_comparison"] = {"compare_working": str(args.compare_working)}
        for lvl in ("division", "group"):
            x = h[h.for_level == lvl].merge(other[other.for_level == lvl], on=["hep", "code"])
            num["snapshot_comparison"][lvl] = {
                "n": len(x),
                "rs": float(spearmanr(x.v_x, x.v_y)[0]),
                "rs_within": float(spearmanr(x.groupby("code").v_x.rank(pct=True),
                                             x.groupby("code").v_y.rank(pct=True))[0])}

    make_figure(div, figures / "fig_era2018_v_by_rating.pdf")

    (HERE / "era2018_numbers.json").write_text(json.dumps(num, indent=1, default=float))
    print(json.dumps({k: num[k] for k in ("n_rated", "n_matched", "pooled", "share_rated_ge4")},
                     indent=1))
    for md in ("ALL", "CA", "PR"):
        for k, r in reg[md].items():
            print(md, k, {c: (round(v["b"], 3), round(v["se"], 3), f"{v['p']:.3g}")
                          for c, v in r.items() if isinstance(v, dict)})


def committee_flags(rec: dict, keys: pd.DataFrame) -> set:
    """(REC, HEP) pairs where the HEP had at least one member (chair included)."""
    names = {str(o).lower().replace("the ", "").strip(): h for o, h in zip(keys.Organisation, keys.HEP)}
    alias = {"university of technology, sydney": "UTS", "university of new south wales": "NSW"}
    out = set()
    for code, r in rec.items():
        for p in [r["chair"]] + r["members"]:
            a = p["affiliation"].lower().replace("the ", "").strip()
            hep = alias.get(a) or names.get(a)
            if hep:
                out.add((code, hep))
    return out


def make_figure(div: pd.DataFrame, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.2), sharey=True)
    for ax, md in zip(axes, ("CA", "PR")):
        g = div[div["mode"] == md]
        for r in range(1, 6):
            y = g.loc[g.era == r, "log_v"]
            if y.empty:
                continue
            x = r + RNG.uniform(-0.18, 0.18, len(y))
            ax.scatter(x, y, s=9, color="#4a6fa5", alpha=0.55, linewidths=0)
            ax.hlines(y.median(), r - 0.3, r + 0.3, color="#1b1b1b", lw=1.6)
        ax.axhline(0, color="#999999", lw=0.8, ls="--")
        ax.set_xticks(range(1, 6))
        ax.set_xlabel("ERA 2018 rating")
        ax.set_title(f"{MODE_NAME[md]} disciplines (n = {len(g)})", fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel(r"$\log_{10} v$")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
