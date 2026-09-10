# Equity-Income ETF Total Returns

Calculates distribution-adjusted total returns for equity-income ETFs over the
last 1 month, 3 months, 6 months, and 1 year. The report includes S&P 500,
Nasdaq-100, and Dow-oriented income strategies plus plain-index benchmarks.

## Generated files

Running the script creates these files in the repository root:

- `etf_total_returns.csv`
- `etf_total_returns.png`

Yahoo Finance adjusted closing prices are used so dividends, distributions,
and stock splits are incorporated into total return.

## Run locally

```bash
python -m pip install --requirement requirements.txt
python etf_total_returns.py
```

Select specific funds:

```bash
python etf_total_returns.py --tickers GPIX GPIQ ISPY QYLG XYLG DIVO VOO QQQM DIA
```

Calculate as of a historical date:

```bash
python etf_total_returns.py --as-of 2026-08-31
```

## Run from GitHub

1. Open the repository's **Actions** tab.
2. Select **Update ETF total returns**.
3. Select **Run workflow**.
4. Optionally enter an as-of date or a space-separated ticker list.

The workflow checks out `main`, creates the CSV and PNG at the repository root,
and commits changed output files back to `main`.

The repository's workflow permissions must allow GitHub Actions to write to the
repository. If `main` is protected, its rules must permit the GitHub Actions bot
to push, or the commit step must be changed to open a pull request.
