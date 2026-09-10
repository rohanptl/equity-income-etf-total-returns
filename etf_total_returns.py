#!/usr/bin/env python3
"""Calculate trailing total returns for equity-income ETFs.

Total return is calculated from Yahoo Finance adjusted closing prices, which
incorporate dividends/distributions and stock splits. Lookback returns use the
last available trading close on or before the calendar lookback date.

Install dependencies:
    python -m pip install -r requirements.txt

Examples:
    python etf_total_returns.py
    python etf_total_returns.py --tickers GPIX GPIQ QQQI SPYI VOO QQQM
    python etf_total_returns.py --as-of 2026-08-31 --output returns.csv --chart returns.png
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import pandas as pd
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - depends on local environment
    missing = getattr(exc, "name", "required package")
    raise SystemExit(
        f"Missing dependency: {missing}. "
        "Install dependencies with: python -m pip install -r requirements.txt"
    ) from exc


FUND_GROUPS: dict[str, str] = {
    # S&P 500 income strategies
    "GPIX": "S&P 500 income",
    "ISPY": "S&P 500 income",
    "XYLG": "S&P 500 income",
    "SPYI": "S&P 500 income",
    "VOOY": "US large-cap income",
    "XYLD": "S&P 500 income",
    "OVL": "S&P 500 income",
    # Nasdaq-oriented income strategies
    "GPIQ": "Nasdaq-100 income",
    "QYLG": "Nasdaq-100 income",
    "QQQI": "Nasdaq-100 income",
    "ROCQ": "Nasdaq income",
    "XQQI": "Nasdaq-100 income",
    "QQQY": "Nasdaq-100 income",
    "QYLD": "Nasdaq-100 income",
    "TDAQ": "Nasdaq-100 income",
    # Dow-oriented strategies
    "DIVO": "Dow-like equity income",
    "DJIA": "Dow 30 income",
    # Plain-index benchmarks
    "VOO": "S&P 500 benchmark",
    "QQQM": "Nasdaq-100 benchmark",
    "DIA": "Dow 30 benchmark",
}

DEFAULT_TICKERS = list(FUND_GROUPS)


@dataclass(frozen=True)
class Period:
    label: str
    months: int


PERIODS = (
    Period("1M", 1),
    Period("3M", 3),
    Period("6M", 6),
    Period("1Y", 12),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate distribution-adjusted 1M, 3M, 6M and 1Y total returns "
            "for equity-income ETFs."
        )
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=DEFAULT_TICKERS,
        help="Space-separated tickers. Defaults to the comparison universe.",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Evaluation date. Defaults to today (latest available close).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("etf_total_returns.csv"),
        help="CSV output path (default: etf_total_returns.csv).",
    )
    parser.add_argument(
        "--chart",
        type=Path,
        default=Path("etf_total_returns.png"),
        help="PNG chart output path (default: etf_total_returns.png).",
    )
    parser.add_argument(
        "--no-benchmarks",
        action="store_true",
        help="Remove VOO, QQQM and DIA when using the default ticker universe.",
    )
    return parser.parse_args()


def normalize_tickers(raw_tickers: list[str]) -> list[str]:
    """Normalize symbols and preserve input order while removing duplicates."""
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_tickers:
        ticker = value.strip().upper()
        if ticker and ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    if not result:
        raise ValueError("At least one ticker is required.")
    return result


def parse_as_of(value: str | None) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp(date.today())
    try:
        parsed = pd.Timestamp(value)
    except ValueError as exc:
        raise ValueError("--as-of must use YYYY-MM-DD format.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.tz_localize(None)
    return parsed.normalize()


def download_adjusted_closes(
    tickers: list[str], as_of: pd.Timestamp
) -> dict[str, pd.Series]:
    """Download adjusted close history in one request and split by ticker."""
    start = (as_of - pd.DateOffset(months=12, days=14)).date().isoformat()
    # yfinance's end date is exclusive, so add one day to include --as-of.
    end = (as_of + pd.Timedelta(days=1)).date().isoformat()

    data = yf.download(
        tickers=tickers,
        start=start,
        end=end,
        auto_adjust=True,
        actions=False,
        group_by="ticker",
        threads=True,
        progress=False,
        repair=True,
    )

    if data.empty:
        raise RuntimeError("Yahoo Finance returned no price data.")

    closes: dict[str, pd.Series] = {}

    def clean(series: pd.Series) -> pd.Series:
        series = series.dropna().astype(float).sort_index()
        index = pd.DatetimeIndex(series.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        series.index = index
        return series[~series.index.duplicated(keep="last")]

    if isinstance(data.columns, pd.MultiIndex):
        top_level = set(data.columns.get_level_values(0))
        for ticker in tickers:
            if ticker not in top_level or "Close" not in data[ticker].columns:
                continue
            series = clean(data[ticker]["Close"])
            if not series.empty:
                closes[ticker] = series
    else:
        # yfinance uses a single column level when only one ticker is requested.
        if "Close" in data.columns:
            series = clean(data["Close"])
            if not series.empty:
                closes[tickers[0]] = series

    return closes


def price_on_or_before(series: pd.Series, target: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    eligible = series.loc[series.index <= target]
    if eligible.empty:
        return None
    return eligible.index[-1], float(eligible.iloc[-1])


def calculate_returns(
    tickers: list[str], closes: dict[str, pd.Series], as_of: pd.Timestamp
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for ticker in tickers:
        row: dict[str, object] = {
            "Ticker": ticker,
            "Group": FUND_GROUPS.get(ticker, "Custom"),
            "Latest Date": pd.NaT,
            "Adjusted Close": pd.NA,
        }
        for period in PERIODS:
            row[f"{period.label} Total Return"] = pd.NA

        series = closes.get(ticker)
        if series is None or series.empty:
            row["Status"] = "No data"
            rows.append(row)
            continue

        latest = price_on_or_before(series, as_of)
        if latest is None:
            row["Status"] = "No close on/before as-of date"
            rows.append(row)
            continue

        latest_date, latest_price = latest
        row["Latest Date"] = latest_date.date().isoformat()
        row["Adjusted Close"] = latest_price

        available_periods = 0
        for period in PERIODS:
            target = as_of - pd.DateOffset(months=period.months)
            starting = price_on_or_before(series, target)
            if starting is None:
                continue
            _, starting_price = starting
            row[f"{period.label} Total Return"] = latest_price / starting_price - 1.0
            available_periods += 1

        row["Status"] = "OK" if available_periods == len(PERIODS) else "Limited history"
        rows.append(row)

    return pd.DataFrame(rows)


def format_console_table(results: pd.DataFrame) -> str:
    display = results.copy()
    display["Adjusted Close"] = display["Adjusted Close"].map(
        lambda value: "N/A" if pd.isna(value) else f"${float(value):,.2f}"
    )
    for period in PERIODS:
        column = f"{period.label} Total Return"
        display[column] = display[column].map(
            lambda value: "N/A" if pd.isna(value) else f"{float(value):+.2%}"
        )
    return display.to_string(index=False)


def save_csv(results: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output, index=False, float_format="%.8f")


def group_color(group: str) -> str:
    if "benchmark" in group.lower():
        return "#475569"
    if "nasdaq" in group.lower():
        return "#f59e0b"
    if "dow" in group.lower():
        return "#10b981"
    if "s&p" in group.lower() or "large-cap" in group.lower():
        return "#2563eb"
    return "#8b5cf6"


def save_chart(results: pd.DataFrame, output: Path, as_of: pd.Timestamp) -> None:
    """Create a four-panel horizontal bar chart of trailing total returns."""
    plot_data = results.copy()
    return_columns = [f"{period.label} Total Return" for period in PERIODS]
    for column in return_columns:
        plot_data[column] = pd.to_numeric(plot_data[column], errors="coerce")

    # Keep one ticker order across panels so comparisons remain easy to scan.
    plot_data["_sort"] = plot_data["1Y Total Return"].fillna(
        plot_data["6M Total Return"].fillna(float("-inf"))
    )
    plot_data = plot_data.sort_values("_sort", ascending=True).reset_index(drop=True)

    figure_height = max(9.0, 0.48 * len(plot_data) + 2.6)
    fig, axes = plt.subplots(
        1,
        len(PERIODS),
        figsize=(18, figure_height),
        sharey=True,
    )
    fig.subplots_adjust(left=0.07, right=0.99, top=0.91, bottom=0.12, wspace=0.16)
    colors = [group_color(group) for group in plot_data["Group"]]

    for axis, period in zip(axes, PERIODS, strict=True):
        column = f"{period.label} Total Return"
        values = plot_data[column] * 100.0
        bars = axis.barh(plot_data["Ticker"], values, color=colors, alpha=0.9)
        axis.axvline(0, color="#0f172a", linewidth=0.9)
        axis.grid(axis="x", color="#cbd5e1", linewidth=0.6, alpha=0.65)
        axis.set_axisbelow(True)
        axis.set_title(period.label, fontsize=13, fontweight="bold")
        axis.set_xlabel("Total return (%)")
        axis.tick_params(axis="y", length=0)
        axis.spines[["top", "right", "left"]].set_visible(False)

        visible_values = values.dropna()
        span = max(abs(visible_values).max(), 1.0) if not visible_values.empty else 1.0
        if not visible_values.empty:
            lower = min(0.0, float(visible_values.min()))
            upper = max(0.0, float(visible_values.max()))
            padding = max((upper - lower) * 0.12, 1.0)
            axis.set_xlim(lower - padding, upper + padding)
        for bar, value in zip(bars, values, strict=True):
            if pd.isna(value):
                bar.set_visible(False)
                continue
            offset = span * 0.025
            x = value + offset if value >= 0 else value - offset
            alignment = "left" if value >= 0 else "right"
            axis.text(
                x,
                bar.get_y() + bar.get_height() / 2,
                f"{value:+.1f}%",
                va="center",
                ha=alignment,
                fontsize=8,
            )

    axes[0].tick_params(axis="y", labelsize=10)
    fig.suptitle(
        "Equity-Income ETF Total Returns",
        fontsize=18,
        fontweight="bold",
        y=0.975,
    )
    legend = [
        Patch(facecolor="#2563eb", label="S&P 500 / large-cap income"),
        Patch(facecolor="#f59e0b", label="Nasdaq income"),
        Patch(facecolor="#10b981", label="Dow-oriented income"),
        Patch(facecolor="#475569", label="Plain-index benchmark"),
    ]
    fig.legend(
        handles=legend,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.045),
        ncol=4,
        frameon=False,
    )
    fig.text(
        0.5,
        0.018,
        (
            f"Through {as_of.date().isoformat()} · Adjusted closes include "
            "reinvested dividends/distributions · Source: Yahoo Finance"
        ),
        ha="center",
        fontsize=9,
        color="#475569",
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    try:
        tickers = normalize_tickers(args.tickers)
        if args.no_benchmarks:
            tickers = [ticker for ticker in tickers if ticker not in {"VOO", "QQQM", "DIA"}]

        as_of = parse_as_of(args.as_of)
        closes = download_adjusted_closes(tickers, as_of)
        results = calculate_returns(tickers, closes, as_of)
        save_csv(results, args.output)
        save_chart(results, args.chart, as_of)

        print(f"Total returns through {as_of.date().isoformat()}")
        print("Adjusted prices include reinvested dividends and distributions.\n")
        print(format_console_table(results))
        print(f"\nSaved CSV: {args.output.resolve()}")
        print(f"Saved chart: {args.chart.resolve()}")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
