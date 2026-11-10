"""
Solar data and calculations for the FiT dashboard.

Data: daily GB grid-connected solar generation (GWh) and installed capacity (MWp),
summed from Sheffield Solar PV_Live half-hourly outturn. Columns: date, generation_gwh, capacity_mwp.

Scheme (delivery) years run 1 April to 31 March. 29 February is dropped so every year
has 365 comparable days, indexed 0 (1 April) to 364 (31 March).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

PVLIVE_URL = "https://api.pvlive.uk/pvlive/api/v4/gsp/0"
APP_DIR = Path(__file__).parent
# The data file is normally data/solar_daily.csv; a copy next to app.py also works.
DATA_CANDIDATES = [APP_DIR / "data" / "solar_daily.csv", APP_DIR / "solar_daily.csv"]
DATA_FILE = next((p for p in DATA_CANDIDATES if p.exists()), DATA_CANDIDATES[0])

# Month starts as day-of-scheme-year index, for chart ticks
MONTH_TICKS = [(0, "Apr"), (30, "May"), (61, "Jun"), (91, "Jul"), (122, "Aug"), (153, "Sep"),
               (183, "Oct"), (214, "Nov"), (244, "Dec"), (275, "Jan"), (306, "Feb"), (334, "Mar")]


# ---------------------------------------------------------------- data loading

def load_daily(path: Path = DATA_FILE) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.dropna(subset=["generation_gwh", "capacity_mwp"])
    df = df[df["capacity_mwp"] > 0].drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


def fetch_pvlive(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Download half-hourly national PV_Live outturn between start (inclusive) and end (exclusive)
    and return daily totals. Raises requests.RequestException if the API can't be reached."""
    frames, cur = [], start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=31), end)
        params = {"start": f"{cur.isoformat()}T00:00:00", "end": f"{nxt.isoformat()}T00:00:00",
                  "extra_fields": "installedcapacity_mwp"}
        r = requests.get(PVLIVE_URL, params=params, timeout=60)
        r.raise_for_status()
        js = r.json()
        frames.append(pd.DataFrame(js["data"], columns=js["meta"]))
        cur = nxt
        time.sleep(0.3)
    if not frames:
        return pd.DataFrame(columns=["date", "generation_gwh", "capacity_mwp"])
    hh = pd.concat(frames, ignore_index=True)
    hh["datetime_gmt"] = pd.to_datetime(hh["datetime_gmt"], utc=True)
    hh = hh.drop_duplicates("datetime_gmt")
    # Timestamps mark the end of each half hour: shift back so 00:00 belongs to the previous day
    hh["date"] = (hh["datetime_gmt"] - pd.Timedelta(minutes=30)).dt.tz_localize(None).dt.normalize()
    hh["generation_mw"] = pd.to_numeric(hh["generation_mw"], errors="coerce").fillna(0)
    daily = hh.groupby("date").agg(generation_gwh=("generation_mw", lambda s: s.sum() * 0.5 / 1000),
                                   capacity_mwp=("installedcapacity_mwp", "max"),
                                   periods=("generation_mw", "size")).reset_index()
    daily = daily[(daily["periods"] >= 46)
                  & (daily["date"] >= pd.Timestamp(start)) & (daily["date"] < pd.Timestamp(end))]
    daily["generation_gwh"] = daily["generation_gwh"].round(1)
    daily["capacity_mwp"] = daily["capacity_mwp"].round(0)
    return daily[["date", "generation_gwh", "capacity_mwp"]]


def update_from_pvlive(path: Path = DATA_FILE) -> int:
    """Append any days missing since the last saved date up to yesterday. Returns days added."""
    df = load_daily(path)
    start = (df["date"].max() + pd.Timedelta(days=1)).date()
    end = dt.date.today()
    if start >= end:
        return 0
    new = fetch_pvlive(start, end)
    if new.empty:
        return 0
    out = pd.concat([df, new]).drop_duplicates("date", keep="last").sort_values("date")
    out.assign(date=out["date"].dt.date).to_csv(path, index=False)
    return len(new)


def create_from_pvlive(start: dt.date = dt.date(2016, 4, 1), path: Path = DATA_FILE) -> int:
    """Build the data file from scratch by downloading everything from `start` to yesterday."""
    daily = fetch_pvlive(start, dt.date.today())
    path.parent.mkdir(parents=True, exist_ok=True)
    daily.assign(date=daily["date"].dt.date).to_csv(path, index=False)
    return len(daily)


def backfill(start: dt.date, end: dt.date, path: Path = DATA_FILE) -> int:
    """Download a past period (start inclusive, end exclusive) and merge it into the data file,
    e.g. backfill(date(2020, 4, 1), date(2021, 4, 1)) for the missing 2020/21. Returns days added."""
    df = load_daily(path)
    new = fetch_pvlive(start, end)
    out = pd.concat([df, new]).drop_duplicates("date", keep="last").sort_values("date")
    out.assign(date=out["date"].dt.date).to_csv(path, index=False)
    return len(out) - len(df)


# ---------------------------------------------------------------- shaping

def scheme_year(d: pd.Timestamp) -> int:
    return d.year if d.month >= 4 else d.year - 1


def label(fy: int) -> str:
    return f"{fy}/{str(fy + 1)[2:]}"


def add_calendar(df: pd.DataFrame) -> pd.DataFrame:
    df = df[~((df["date"].dt.month == 2) & (df["date"].dt.day == 29))].copy()
    df["fy"] = df["date"].map(scheme_year)
    ref = df["date"].map(lambda d: dt.date(2025 if d.month >= 4 else 2026, d.month, d.day))
    df["day"] = (pd.to_datetime(ref) - pd.Timestamp(2025, 4, 1)).dt.days
    df["load_factor"] = df["generation_gwh"] / (df["capacity_mwp"] * 24 / 1000)
    return df


def year_matrix(df: pd.DataFrame, measure: str, latest_cap: float) -> pd.DataFrame:
    """Rows = day index 0..364, columns = scheme years. 'actual' = GWh as generated;
    'adjusted' = each day's load factor x today's capacity, so years compare weather not panels."""
    col = df["generation_gwh"] if measure == "actual" else df["load_factor"] * latest_cap * 24 / 1000
    m = df.assign(v=col).pivot_table(index="day", columns="fy", values="v")
    return m.reindex(range(365))


def day_labels() -> list[str]:
    base = dt.date(2025, 4, 1)
    return [f"{(base + dt.timedelta(days=i)).day} {(base + dt.timedelta(days=i)):%b}" for i in range(365)]


def bands(df: pd.DataFrame, lookback: int, measure: str, smooth: int) -> dict:
    """Current scheme year against the previous `lookback` scheme years, by day of year."""
    df = add_calendar(df)
    latest = df.iloc[-1]
    current_fy = int(latest["fy"])
    m = year_matrix(df, measure, float(latest["capacity_mwp"]))
    past = [y for y in range(current_fy - lookback, current_fy) if y in m.columns]
    cur = m[current_fy] if current_fy in m.columns else pd.Series(np.nan, index=m.index)
    hist = m[past]

    def sm(x):
        return x.rolling(smooth, min_periods=1).mean() if smooth > 1 else x

    cur_s, hist_s = sm(cur).where(cur.notna()), sm(hist).where(hist.notna())
    daily = pd.DataFrame({
        "day": m.index, "date_label": day_labels(), "current": cur_s,
        "min": hist_s.min(axis=1), "max": hist_s.max(axis=1), "mean": hist_s.mean(axis=1),
        "sd": hist_s.std(axis=1, ddof=0),
    })
    daily["upper_2sd"] = daily["mean"] + 2 * daily["sd"]
    daily["lower_2sd"] = (daily["mean"] - 2 * daily["sd"]).clip(lower=0)

    last_day = int(cur.last_valid_index()) if cur.notna().any() else -1
    cur_cum = cur.fillna(0).cumsum().where(cur.index <= last_day)
    hist_cum = hist.fillna(0).cumsum()
    cum = pd.DataFrame({
        "day": m.index, "date_label": day_labels(), "current": cur_cum,
        "min": hist_cum.min(axis=1), "max": hist_cum.max(axis=1), "mean": hist_cum.mean(axis=1),
        "sd": hist_cum.std(axis=1, ddof=0),
    })
    cum["upper_2sd"] = cum["mean"] + 2 * cum["sd"]
    cum["lower_2sd"] = (cum["mean"] - 2 * cum["sd"]).clip(lower=0)

    return {"daily": daily, "cumulative": cum, "current_fy": current_fy, "years": past,
            "last_day": last_day, "last_date": latest["date"], "latest_cap": float(latest["capacity_mwp"]),
            "matrix": m}


def ytd_summary(b: dict) -> dict:
    c = b["cumulative"]
    if b["last_day"] < 0:
        return {}
    r = c.loc[b["last_day"]]
    span = r["max"] - r["min"]
    return {"ytd_gwh": r["current"], "mean_gwh": r["mean"],
            "vs_mean_pct": (r["current"] / r["mean"] - 1) * 100,
            "range_position_pct": (r["current"] - r["min"]) / span * 100 if span > 0 else np.nan}


# ---------------------------------------------------------------- weather scenarios

SCENARIO_METHODS = {
    "P10 / P50 / P90": (lambda v: np.percentile(v, 10), lambda v: np.percentile(v, 50), lambda v: np.percentile(v, 90)),
    "Min / mean / max": (np.min, np.mean, np.max),
    "Mean ± 1 standard deviation": (lambda v: v.mean() - v.std(), np.mean, lambda v: v.mean() + v.std()),
}


def weather_scenarios(df: pd.DataFrame, lookback: int, method: str, next_year_capacity_growth_pct: float = 0.0) -> pd.DataFrame:
    """Low, central and high solar generation, with every historic year's weather replayed at
    today's installed capacity.

    Current year: actual generation to date + each historic year's rest-of-year weather.
    Next year: each historic year's full-year weather, at today's capacity x (1 + growth).
    Weather factor = scenario load factor / the historic average load factor for the same period,
    the figure to feed into the FiT cost model's solar weather factor.
    """
    b = bands(df, lookback, "adjusted", 1)
    m, fy, last_day, cap = b["matrix"], b["current_fy"], b["last_day"], b["latest_cap"]
    years = b["years"]
    hist = m[years]
    ytd = float(m[fy].iloc[: last_day + 1].sum()) if fy in m.columns else 0.0
    rest = hist.iloc[last_day + 1:].sum()           # GWh per historic year, rest of current year
    full = hist.sum()                                # GWh per historic year, full year
    growth = 1 + next_year_capacity_growth_pct / 100
    lo, mid, hi = SCENARIO_METHODS[method]
    rows = []
    for name, fn in (("Low", lo), ("Central", mid), ("High", hi)):
        r = float(fn(rest.values))
        f = float(fn(full.values))
        cur_full = ytd + r
        rows.append({
            "Scenario": name,
            f"{label(fy)} rest of year (GWh)": r,
            f"{label(fy)} full year (GWh)": cur_full,
            f"{label(fy)} vs average year": (cur_full / ytd_mean_plus_rest(m, years, last_day) - 1) * 100,
            f"{label(fy)} full-year weather factor": cur_full / ytd_mean_plus_rest(m, years, last_day),
            f"{label(fy)} rest-of-year weather factor": r / rest.mean() if rest.mean() > 0 else np.nan,
            f"{label(fy + 1)} full year (GWh)": f * growth,
            f"{label(fy + 1)} load factor": f / (cap * 8760 / 1000) * 100,
            f"{label(fy + 1)} weather factor": f / full.mean(),
        })
    out = pd.DataFrame(rows).set_index("Scenario")
    out.attrs.update({"years": years, "ytd_gwh": ytd, "last_date": b["last_date"], "capacity_mwp": cap,
                      "rest_days": 364 - last_day})
    return out


def ytd_mean_plus_rest(m: pd.DataFrame, years: list[int], last_day: int) -> float:
    """Average historic full-year generation at today's capacity (the 'average year')."""
    return float(m[years].sum().mean())


def year_by_year(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Each historic scheme year's weather at today's capacity, for the table under the scenarios."""
    b = bands(df, lookback, "adjusted", 1)
    m, years, last_day = b["matrix"], b["years"], b["last_day"]
    full = m[years].sum()
    rest = m[years].iloc[last_day + 1:].sum()
    days = m[years].notna().sum()
    return pd.DataFrame({
        "Scheme year": [label(y) for y in years],
        "Full year at today's capacity (GWh)": full.values,
        "Weather factor": (full / full.mean()).values,
        "Rest-of-year weather factor": (rest / rest.mean()).values,
        "Days of data": days.values,
    })
