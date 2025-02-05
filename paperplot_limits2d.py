import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import argparse
import uproot
import glob
import mplhep as hep

matplotlib.style.use(hep.style.CMS)


def construct_limits_table(base_dir: str):
    """Aggregating all limits as single array dictionary"""
    return_array = None
    for file in glob.glob(base_dir + "/*.root"):
        try:
            with uproot.open(file) as f:
                if return_array is None:
                    return_array = f["limit"].arrays()
                else:
                    return_array = {
                        k: np.concatenate([return_array[k], f["limit"].arrays()[k]])
                        for k in return_array.keys()
                    }
        except:  # Guard against malformed files
            pass
    return {k.decode("utf8"): v for k, v in return_array.items()}


def make_interpolator(table, mDark: float, quantile=None, observed=None, xsec=False):
    """Returning the interpolation line"""
    assert not (quantile is None and observed is None), (
        "Must specifiy either quantile or observed"
    )
    assert not (quantile is not None and observed is not None), (
        "Cannot specify both quantile and observed"
    )
    x = table["trackedParam_mZprime"]
    y = table["trackedParam_rinv"]
    z = table["limit"]
    if xsec == True:
        z = z * table["trackedParam_xsec"]
    if observed is True:
        quantile = -1

    filter = (table["quantileExpected"] == quantile) & (
        table["trackedParam_mDark"] == mDark
    )
    (x, y, z) = x[filter], y[filter], z[filter]
    return matplotlib.tri.LinearTriInterpolator(matplotlib.tri.Triangulation(x, y), z)


def _make_mesh(n_entries):
    """Slightly shifting the lower boundary for mMed to avoid axis tick clashing"""
    return np.meshgrid(np.linspace(201, 550, n_entries), np.linspace(0, 1, n_entries))


def plot_2d_color(ax, interp, n_entries=50):
    x, y = _make_mesh(n_entries)
    im = ax.pcolormesh(
        *(x, y, interp(x, y)),
        # cmap=plt.cm.Blues,
        norm=matplotlib.colors.LogNorm(vmin=3e-1, vmax=3e2),
        linewidth=0.0,
        edgecolors="None",
    )
    cbar = ax.figure.colorbar(im, ax=ax)
    cbar.ax.set_ylabel("95% CL upper limit on $\sigma$ B [pb]", va="top")
    return ax


def plot_limit_contour(ax, interp, n_entries=50, color="red", **kwargs):
    x, y = _make_mesh(n_entries)
    ax.contour(*(x, y, interp(x, y)), levels=np.array([1]), colors=color, **kwargs)
    return ax.plot([], [], color=color, **kwargs)


def plot_limit_band(ax, interp1, interp2, n_entries=2000, color="red", **kwargs):
    x, y = _make_mesh(n_entries)
    z = (interp1(x, y) < 1) ^ (interp2(x, y) < 1)
    ax.contourf(
        *(x, y, z),
        levels=np.array([0.5, 2]),
        cmap=matplotlib.colors.ListedColormap([color]),
        alpha=0.2,
    )
    return ax.fill(np.NaN, np.NaN, color=color, alpha=0.2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser("Script for plotting limits ready for publication")
    parser.add_argument(
        "--base_dir",
        "-d",
        type=str,
        default="Limits",
        help="Directory containing the root files with the limit information; files here should be generated by the cls_maker.py script",
    )
    parser.add_argument(
        "--mDark",
        "-m",
        type=float,
        default=10,
        choices=[10],
        help="Dark meson mass point to use for plotting",
    )
    parser.add_argument(
        "--label", type=str, default="Preliminary", help="Plot label used for display"
    )
    parser.add_argument(
        "--observed",
        "-o",
        type=str,
        default="False",
        choices=["True", "False", "Dummy"],
        help="Whether or not to include the observed limit",
    )
    args = parser.parse_args()

    table = construct_limits_table(args.base_dir)

    fig = plt.figure(constrained_layout=True, figsize=(11, 11))
    spec = fig.add_gridspec(ncols=1, nrows=1, width_ratios=[1], height_ratios=[1])
    ax = fig.add_subplot(spec[0, 0])
    ax.set_xlabel("", horizontalalignment="right", x=1.0)
    ax.set_ylabel("", horizontalalignment="right", y=1.0)

    legend_entries = {}

    exp_central_fb = make_interpolator(table, mDark=args.mDark, quantile=0.5, xsec=True)
    plot_2d_color(ax, exp_central_fb)

    exp_central = make_interpolator(table, mDark=args.mDark, quantile=0.5)
    exp_up = make_interpolator(table, mDark=args.mDark, quantile=0.16)
    exp_lo = make_interpolator(table, mDark=args.mDark, quantile=0.84)
    p1 = plot_limit_contour(ax, exp_central, color="red")
    p2 = plot_limit_band(ax, exp_up, exp_lo, color="red")
    legend_entries[r"Exp. limit $\pm 1\sigma_{exp}$"] = (p1[0], p2[0])

    if args.observed != "False":
        obs = make_interpolator(
            table, mDark=args.mDark, quantile=-1 if args.observed == "True" else 0.16
        )
        po = plot_limit_contour(ax, obs, color="black")
        legend_entries[
            "Obs. limit (dummy)" if args.observed == "Dummy" else "Obs. limit"
        ] = po[0]

    hep.cms.text(text=args.label, ax=ax, loc=0)
    hep.cms.lumitext(text="138 $fb^{-1}$ (13 TeV)")

    leg = ax.legend(
        list(legend_entries.values()),
        list(legend_entries.keys()),
        title="$m_{dark}$ = " + str(args.mDark) + " GeV",
        loc="upper right",
        frameon=True,
    )
    leg._legend_box.align = "left"

    ax.set_xlabel("$m_{Z^{\prime}}$ [GeV]")
    ax.set_ylabel("$r_{inv}$")
    fig.savefig(f"limits2d_{args.label.replace(' ', '-')}_mDark-{args.mDark}.pdf")
