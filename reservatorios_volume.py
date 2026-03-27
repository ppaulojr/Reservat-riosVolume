#!/usr/bin/env python3
"""
Reservatórios Volume - Brazilian Reservoir Storage Level Visualization

Downloads daily EAR (Energia Armazenada - Stored Energy) data by subsystem
from the ONS (Operador Nacional do Sistema Elétrico) open data portal and
generates comparative year-over-year charts for each subsystem.

Data source:
    https://dados.ons.org.br/dataset/ear-diario-por-subsistema

Usage:
    python reservatorios_volume.py [--start-year YEAR] [--end-year YEAR] [--output FILE]

Examples:
    python reservatorios_volume.py
    python reservatorios_volume.py --start-year 2022 --end-year 2026
    python reservatorios_volume.py --output reservatorios.png
"""

import argparse
import sys
import warnings
from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# ONS open data base URL for daily EAR by subsystem (parquet format)
BASE_URL = (
    "https://ons-aws-prod-opendata.s3.amazonaws.com"
    "/dataset/ear_subsistema_di/EAR_DIARIO_SUBSISTEMA_"
)

# Column names in the ONS dataset
COL_DATE = "ear_data"
COL_SUBSYSTEM = "nom_subsistema"
COL_EAR_PCT = "ear_verif_subsistema_percentual"


def download_data(start_year: int, end_year: int) -> pd.DataFrame:
    """Download EAR daily data for the given year range.

    Parameters
    ----------
    start_year : int
        First year to download (inclusive).
    end_year : int
        Last year to download (inclusive).

    Returns
    -------
    pd.DataFrame
        Concatenated dataframe with all downloaded years.
    """
    print("🚀 Downloading daily EAR dataset by subsystem...\n")

    frames: list[pd.DataFrame] = []
    for year in range(start_year, end_year + 1):
        url = f"{BASE_URL}{year}.parquet"
        print(f"   Trying {year}...")
        try:
            response = requests.get(url, verify=False, timeout=90)
            response.raise_for_status()
            df_year = pd.read_parquet(BytesIO(response.content), engine="pyarrow")
            frames.append(df_year)
            print(f"      ✅ {year} loaded ({len(df_year):,} rows)")
        except Exception as exc:
            print(f"      ❌ Error {year}: {exc}")

    if not frames:
        print("\n❌ No data was downloaded. Exiting.")
        sys.exit(1)

    df = pd.concat(frames, ignore_index=True)
    print(f"\n✅ Total loaded: {len(df):,} rows\n")
    return df


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean, enrich and aggregate the raw dataframe.

    Steps:
      1. Convert EAR percentage to numeric and drop NaN.
      2. Parse date column and derive ``year`` / ``day_of_year``.
      3. Group by (year, day_of_year, subsystem) and compute daily mean.
      4. Compute a 7-day rolling average per subsystem-year.

    Parameters
    ----------
    df : pd.DataFrame
        Raw dataframe from :func:`download_data`.

    Returns
    -------
    pd.DataFrame
        Aggregated dataframe ready for plotting.
    """
    df[COL_EAR_PCT] = pd.to_numeric(df[COL_EAR_PCT], errors="coerce")
    df = df.dropna(subset=[COL_EAR_PCT])

    df["data"] = pd.to_datetime(df[COL_DATE], errors="coerce")
    df = df.dropna(subset=["data"])

    df["ano"] = df["data"].dt.year
    df["dia_do_ano"] = df["data"].dt.dayofyear

    # Aggregate: daily mean per subsystem
    df_agg = (
        df.groupby(["ano", "dia_do_ano", COL_SUBSYSTEM])[COL_EAR_PCT]
        .mean()
        .reset_index()
    )
    df_agg = df_agg.sort_values(["ano", "dia_do_ano"])

    # 7-day moving average
    df_agg["ear_moving7"] = df_agg.groupby([COL_SUBSYSTEM, "ano"])[
        COL_EAR_PCT
    ].transform(lambda x: x.rolling(window=7, min_periods=1, center=False).mean())

    return df_agg


def order_subsystems(subsystems: np.ndarray) -> list[str]:
    """Return subsystem names with *Sudeste/Centro-Oeste* first."""
    ordered = sorted(subsystems)
    if any("Sudeste" in s for s in ordered):
        ordered = sorted(ordered, key=lambda x: 0 if "Sudeste" in x else 1)
    return ordered


def plot_reservoirs(df_agg: pd.DataFrame, output: str | None = None) -> None:
    """Generate a 2×2 figure comparing yearly EAR curves per subsystem.

    Parameters
    ----------
    df_agg : pd.DataFrame
        Aggregated dataframe from :func:`prepare_data`.
    output : str or None
        If given, save the figure to this path instead of showing it
        interactively.
    """
    subsystems = order_subsystems(df_agg[COL_SUBSYSTEM].unique())

    print("\n📋 Subsystem order for charts:")
    for name in subsystems:
        print(f"   • {name}")

    sns.set_style("whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(20, 14.5), sharex=True)
    axes = axes.flatten()

    for i, sub in enumerate(subsystems):
        ax = axes[i]
        df_sub = df_agg[df_agg[COL_SUBSYSTEM] == sub].copy()

        if df_sub.empty:
            ax.text(
                0.5,
                0.5,
                f"No data for {sub}",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            continue

        years = sorted(df_sub["ano"].unique())
        colors = plt.cm.tab10(np.linspace(0, 1, len(years)))

        for j, year in enumerate(years):
            data = df_sub[df_sub["ano"] == year]
            # Raw curve (translucent)
            ax.plot(
                data["dia_do_ano"],
                data[COL_EAR_PCT],
                color=colors[j],
                linewidth=1.0,
                alpha=0.25,
            )
            # 7-day moving average (solid)
            ax.plot(
                data["dia_do_ano"],
                data["ear_moving7"],
                label=f"{year}",
                linewidth=2.8,
                color=colors[j],
            )

        ax.set_title(f"{sub}", fontsize=14, pad=8, fontweight="bold")
        ax.set_xlabel("Day of Year (1 = January 1st)", fontsize=11)
        ax.set_ylabel("EAR (%)", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.legend(title="Year", title_fontsize=9, fontsize=8.5, loc="upper right")

        # Reference line for the Southeast / Central-West subsystem
        if "Sudeste" in sub or "Centro" in sub:
            ax.axhline(
                y=65,
                color="red",
                linestyle="--",
                alpha=0.7,
                linewidth=1.5,
                label="65% (current reference)",
            )
            ax.legend(title="Year", title_fontsize=9, fontsize=8.5, loc="upper right")

    # Hide unused axes when there are fewer than 4 subsystems
    for k in range(len(subsystems), len(axes)):
        axes[k].set_visible(False)

    fig.suptitle("Reservatórios", fontsize=18, fontweight="bold", y=0.97)

    plt.subplots_adjust(top=0.81, hspace=0.68, wspace=0.28)
    plt.tight_layout(rect=[0, 0, 1, 0.89])

    if output:
        fig.savefig(output, dpi=150, bbox_inches="tight")
        print(f"\n✅ Figure saved to {output}")
    else:
        plt.show()
        print("\n✅ 2×2 figure generated!")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Visualize Brazilian reservoir storage levels over time.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2020,
        help="First year to download (default: 2020)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2026,
        help="Last year to download (default: 2026)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save figure to file instead of displaying (e.g. output.png)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point: download → prepare → plot."""
    args = parse_args(argv)
    df = download_data(args.start_year, args.end_year)
    df_agg = prepare_data(df)
    plot_reservoirs(df_agg, output=args.output)


if __name__ == "__main__":
    main()
