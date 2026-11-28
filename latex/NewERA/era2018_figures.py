"""
era2018_figures.py — v against global rank figures for the NewERA paper.

Each panel plots log10 v of every institution retained in an emulated ERA 2018 unit against its
global rank, with the Australian HEPs marked by their ERA 2018 rating and, as a star on the same
vertical, their v with the sentinel (epsilon = 1). Called from era2018_analysis.py:

  facet_figure       one division and all its emulated four-digit groups, on the division's
                     rank axis (group panels show group v at the institution's division rank)
  division_grid      division panels only, for a chosen set of divisions (Go8 labelled)
  supplementary_pdf  facet_figure for every division, one page each
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.lines as mlines  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.transforms as mtransforms  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

WINDOW = "2011_2016"

# ERA ratings diverge about world standard (3): blues above, oranges below, grey at.
RATING_STYLE = {
    5: dict(color="#1f5fa8", marker="o", label="5 well above"),
    4: dict(color="#86b5de", marker="s", label="4 above"),
    3: dict(color="#7a7a7a", marker="^", label="3 at"),
    2: dict(color="#f2a36b", marker="v", label="2 below"),
    1: dict(color="#c23b1e", marker="D", label="1 well below"),
    0: dict(color="white", marker="o", label="not rated"),
}
INK = "#222222"
WORLD = "#c4c4c4"
GUIDE = "#d9d9d9"

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 7.5, "axes.labelsize": 8,
    "axes.titlesize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "pdf.fonttype": 42,
})


# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────
def _rank_file(working: Path, code: str, sentinel: bool) -> Path:
    sub, lvl, pre = ("era2018_sentinel" if sentinel else "era2018",
                     "division" if len(code) == 2 else "group",
                     "div" if len(code) == 2 else "grp")
    return working / sub / lvl / f"rankings_{pre}_{code}_{WINDOW}_{'sentinel' if sentinel else 'baseline'}.parquet"


def unit_table(working: Path, code: str) -> pd.DataFrame | None:
    """Institutions retained in a unit: unit_idx, v, rank_v, v_sent (NaN if no sentinel run)."""
    f = _rank_file(working, code, False)
    if not f.exists():
        return None
    d = pd.read_parquet(f, columns=["unit_idx", "unit_type", "v", "rank_v"])
    d = d[d.unit_type == "U"].drop(columns="unit_type")
    fs = _rank_file(working, code, True)
    if fs.exists():
        s = pd.read_parquet(fs, columns=["unit_idx", "unit_type", "v"])
        s = s[s.unit_type == "U"].rename(columns={"v": "v_sent"}).drop(columns="unit_type")
        d = d.merge(s, on="unit_idx", how="left")
    else:
        d["v_sent"] = np.nan
    return d


class Ctx:
    """Shared lookups: HEP keys, ratings, Go8, code titles."""

    def __init__(self, working: Path, data_dir: Path, outcomes: pd.DataFrame, titles: dict):
        keys = pd.read_excel(data_dir / "HEP_concordances.xlsx", sheet_name="Keys")
        keys["inst"] = keys.institution_idx.astype(str).str.lstrip("I").astype("int64")
        self.hep_of = dict(zip(keys.inst, keys.HEP))
        self.go8 = set(keys.loc[keys["Group"].astype(str).str.upper().eq("GO8"), "HEP"])
        self.rating = {(h, c): int(e) for h, c, e in zip(outcomes.hep, outcomes.code, outcomes.era)}
        self.titles = titles
        self.working = working

    def au(self, d: pd.DataFrame, code: str) -> pd.DataFrame:
        a = d[d.unit_idx.isin(self.hep_of)].copy()
        a["hep"] = a.unit_idx.map(self.hep_of)
        a["era"] = [self.rating.get((h, code), 0) for h in a.hep]
        return a


# ─────────────────────────────────────────────────────────────────────────────
# Drawing helpers
# ─────────────────────────────────────────────────────────────────────────────
def _draw_au(ax, a: pd.DataFrame, x: str, size: float = 26):
    """ERA-rated markers at baseline v, sentinel stars on the same vertical, joined by a tick."""
    for _, r in a.iterrows():
        if pd.notna(r.v_sent):
            ax.plot([r[x], r[x]], [np.log10(r.v), np.log10(r.v_sent)], color="#555555", lw=0.5, zorder=4)
    for era, st in RATING_STYLE.items():
        g = a[a.era == era]
        if g.empty:
            continue
        gs = g[g.v_sent.notna()]
        ax.scatter(gs[x], np.log10(gs.v_sent), marker="*", s=size * 1.5, color=st["color"],
                   edgecolor=INK, linewidth=0.45, zorder=5)
        ax.scatter(g[x], np.log10(g.v), marker=st["marker"], s=size, color=st["color"],
                   edgecolor=INK, linewidth=0.5, zorder=6)


def _place_labels(ax, xs: list[float], texts: list[str], xmax: float, ax_w_pt: float,
                  ys: list[float] | None = None):
    """
    HEP codes in tiers above the panel; each label sits on its institution's vertical. With `ys`
    (data y of the marker) a dotted guide also runs from the panel top down to the marker.
    """
    min_dx = 15.0 / ax_w_pt * xmax                   # ~15 pt between labels in one tier
    last: list[float] = []
    trans = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
    n_tiers = 0
    if ys is not None:
        for x, y in zip(xs, ys):
            ax.plot([x, x], [y, ax.get_ylim()[1]], color="#b5b5b5", lw=0.4, ls=":", zorder=1)
    for x, t in sorted(zip(xs, texts)):
        tier = next((k for k, lx in enumerate(last) if x - lx >= min_dx), None)
        if tier is None:
            tier = len(last)
            last.append(x)
        last[tier] = x
        n_tiers = max(n_tiers, tier + 1)
        ax.annotate(t, xy=(x, 1.0), xycoords=trans, xytext=(0, 2.5 + 7.6 * tier),
                    textcoords="offset points", ha="center", va="bottom", fontsize=5.6,
                    color=INK, annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color="#9a9a9a", lw=0.35, shrinkA=0, shrinkB=0))
    return n_tiers


def _style_axis(ax, ylim, xmax, step):
    ax.axhline(0, color=INK, ls=(0, (4, 3)), lw=0.7, zorder=3)
    ax.set_ylim(*ylim)
    ax.set_xlim(0, xmax)
    ax.set_xticks(np.arange(0, xmax + 1, step))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#eeeeee", lw=0.5, zorder=0)


def _ylim(*series) -> tuple[float, float]:
    y = np.log10(pd.concat([s.dropna() for s in series]))
    lo, hi = np.floor((y.min() - 0.05) * 4) / 4, np.ceil((y.max() + 0.05) * 4) / 4
    return max(lo, -2.0), min(hi, 1.5)


def _nice_step(xmax: float) -> int:
    for s in (50, 100, 200, 250, 500, 1000, 2000):
        if xmax / s <= 9:
            return s
    return 5000


def _legend(fig, y: float, with_go8: bool = False):
    h = [mlines.Line2D([], [], ls="none", marker=st["marker"], markersize=4.2, markerfacecolor=st["color"],
                       markeredgecolor=INK, markeredgewidth=0.5, label=st["label"])
         for st in RATING_STYLE.values()]
    h += [mlines.Line2D([], [], ls="none", marker="*", markersize=6, markerfacecolor="#bbbbbb",
                        markeredgecolor=INK, markeredgewidth=0.45, label=r"with sentinel ($\varepsilon=1$)"),
          mlines.Line2D([], [], color=WORLD, lw=1.4, label="all institutions"),
          mlines.Line2D([], [], color=INK, ls=(0, (4, 3)), lw=0.7, label=r"world mean, $v=1$")]
    fig.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, y), ncol=5, frameon=False,
               fontsize=6.3, handletextpad=0.3, columnspacing=0.9,
               title="ERA 2018 rating (Australian HEPs)", title_fontsize=6.5)


# ─────────────────────────────────────────────────────────────────────────────
# Figures
# ─────────────────────────────────────────────────────────────────────────────
def _facet(ctx: Ctx, div: str, fig_w: float = 7.0, title: str | None = None):
    d = unit_table(ctx.working, div)
    if d is None:
        return None
    groups = sorted(p.name.split("_")[2] for p in
                    (ctx.working / "era2018" / "group").glob(f"rankings_grp_{div}??_{WINDOW}_baseline.parquet"))
    panels = [(div, d)] + [(g, unit_table(ctx.working, g)) for g in groups]
    rank_div = dict(zip(d.unit_idx, d.rank_v))
    a_div = ctx.au(d, div)
    xmax = (a_div.rank_v.max() if len(a_div) else len(d)) * 1.06 + 20
    xmax = min(xmax, len(d) * 1.02)
    au_all = []
    for code, t in panels:
        t = t.assign(x=t.unit_idx.map(rank_div)).dropna(subset=["x"])
        au_all.append(ctx.au(t, code))
    ylim = _ylim(*[t.v for _, t in panels if len(t)], *[a.v_sent for a in au_all if len(a)])
    ylim = (max(ylim[0], -1.5), ylim[1])

    n = len(panels)
    panel_h = 1.05
    head = 0.3 if title else 0.0
    fig, axes = plt.subplots(n, 1, figsize=(fig_w, 1.35 + head + n * panel_h), sharex=True,
                             gridspec_kw=dict(hspace=0.12))
    axes = np.atleast_1d(axes)
    for ax, (code, t), a in zip(axes, panels, au_all):
        t = t.assign(x=t.unit_idx.map(rank_div)).dropna(subset=["x"]).sort_values("x")
        for xr in a_div.rank_v:
            ax.axvline(xr, color=GUIDE, lw=0.4, ls=":", zorder=1)
        if code == div:
            ax.plot(t.x, np.log10(t.v), color=WORLD, lw=1.4, zorder=2)
        else:
            ax.scatter(t.x, np.log10(t.v), s=3, color=WORLD, linewidths=0, zorder=2)
        _draw_au(ax, a.assign(x=a.unit_idx.map(rank_div)), "x", size=22)
        _style_axis(ax, ylim, xmax, _nice_step(xmax))
        ax.text(0.005, 0.04, f"{code} {ctx.titles.get(code, '')}  ($N_I$ = {len(t):,}; "
                f"Australian HEPs {len(a)})", transform=ax.transAxes, fontsize=6.6, color=INK,
                va="bottom", ha="left", zorder=7,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=0.8))
        ax.set_ylabel(r"$\log_{10} v$")
    n_tiers = _place_labels(axes[0], list(a_div.rank_v), list(a_div.hep), xmax, fig_w * 72 * 0.92)
    axes[-1].set_xlabel(f"Global rank of institution by $v$ in division {div} {ctx.titles.get(div, '')}")
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.35 / fig.get_figheight() + 0.03,
                        top=1 - (0.50 + head + 0.11 * n_tiers) / fig.get_figheight())
    _legend(fig, 1 - head / fig.get_figheight())
    if title:
        fig.suptitle(title, fontsize=9, y=1 - 0.06 / fig.get_figheight(), va="top", fontweight="bold")
    return fig


def facet_figure(ctx: Ctx, div: str, path: Path):
    fig = _facet(ctx, div)
    fig.savefig(path)
    plt.close(fig)


def supplementary_pdf(ctx: Ctx, path: Path, divisions: list[str]):
    with PdfPages(path) as pdf:
        for div in divisions:
            fig = _facet(ctx, div, title=f"FoR {div} {ctx.titles.get(div, '')}")
            if fig is None:
                continue
            pdf.savefig(fig)
            plt.close(fig)


def division_grid(ctx: Ctx, divisions: list[str], path: Path, ncol: int = 2, fig_w: float = 7.0,
                  ylim: tuple[float, float] = (-1.6, 1.0)):
    """Division panels on a common v scale; Go8 universities labelled."""
    nrow = int(np.ceil(len(divisions) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(fig_w, 0.75 + nrow * 2.25),
                             gridspec_kw=dict(hspace=0.95, wspace=0.16))
    ax_w_pt = fig_w * 72 * 0.92 / ncol
    tiers = []
    for ax, div in zip(axes.flat, divisions):
        d = unit_table(ctx.working, div).sort_values("rank_v")
        a = ctx.au(d, div)
        xmax = min((a.rank_v.max() if len(a) else len(d)) * 1.08 + 20, len(d) * 1.02)
        ax.plot(d.rank_v, np.log10(d.v), color=WORLD, lw=1.4, zorder=2)
        _draw_au(ax, a, "rank_v", size=18)
        _style_axis(ax, ylim, xmax, _nice_step(xmax))
        g8 = a[a.hep.isin(ctx.go8)]
        n_t = _place_labels(ax, list(g8.rank_v), list(g8.hep), xmax, ax_w_pt, ys=list(np.log10(g8.v)))
        tiers.append(n_t)
        ax.set_title(f"{div} {ctx.titles.get(div, '')}  ($N_I$ = {len(d):,})", loc="left", fontsize=7.2,
                     pad=4 + 7.6 * n_t)
        ax.set_xlabel("Global rank by $v$", fontsize=6.8, labelpad=1.5)
        ax.set_ylabel(r"$\log_{10} v$", fontsize=7, labelpad=1.5)
    for ax in axes.flat[len(divisions):]:
        ax.set_visible(False)
    first_row = max(tiers[:ncol])
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.05,
                        top=1 - (0.78 + 0.106 * first_row) / fig.get_figheight())
    _legend(fig, 1.0)
    fig.savefig(path)
    plt.close(fig)
