"""
DSDA 310 Project 3 - Divvy Ridership Forecasting
Stage 1: build the modelling dataset from source.

Runs from a clean clone with no manual steps:

    pip install -r requirements.txt
    python build_dataset.py

Downloads the twelve CY2025 Divvy trip archives and the matching Open-Meteo
ERA5 hourly weather, aggregates rides to local Chicago hour and local date,
merges weather onto rides, and VERIFIES the merge rather than assuming it.

Outputs (data/):
    divvy_hourly_local.csv   rides per local Chicago hour
    weather_hourly_utc.csv   raw weather as pulled, UTC timestamps
    divvy_hourly_merged.csv  rides + weather, local hour grain
    divvy_daily_merged.csv   rides + weather, local date grain  <- forecasting target
    merge_report.txt         the verification log

Units, sources and known gotchas are documented in data/data_description.md.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATA = Path(__file__).resolve().parent / "data"
RAW = DATA / "raw"

DIVVY_URL = "https://divvy-tripdata.s3.amazonaws.com/{ym}-divvy-tripdata.zip"
METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

YEAR = 2025
# Chicago reference point from the assignment brief.
LAT, LON = 41.8781, -87.6298

# Pulled in UTC on purpose - see note in pull_weather().
WEATHER_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "snow_depth",
    "wind_speed_10m",
    "wind_gusts_10m",
    "cloud_cover",
    "weather_code",
    "is_day",
]

# Allow a fast partial run for testing: MONTHS=02,03 python build_dataset.py
MONTHS = [f"{YEAR}{m:02d}" for m in range(1, 13)]
if os.environ.get("MONTHS"):
    MONTHS = [f"{YEAR}{m.strip().zfill(2)[-2:]}"
              for m in os.environ["MONTHS"].split(",")]

log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    log_lines.append(msg)


# ----------------------------------------------------------------------------
# 1. Divvy trips -> rides per local Chicago hour
# ----------------------------------------------------------------------------
def download_divvy(ym: str) -> Path:
    """Fetch one monthly archive, caching it under data/raw/."""
    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / f"{ym}-divvy-tripdata.zip"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = DIVVY_URL.format(ym=ym)
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def read_month(ym: str) -> pd.DataFrame:
    """One month of trips, only the columns the target needs.

    Schema gotchas handled here:
      - start_lat/start_lng/end_lat/end_lng are UNQUOTED while every other
        field is quoted, so a hand-rolled split on '","' silently corrupts
        every row. We use a real CSV parser and never touch those columns.
      - started_at carries millisecond precision; line endings are CRLF.
      - Rides are keyed on started_at, NOT on the file they shipped in.
    """
    path = download_divvy(ym)
    with zipfile.ZipFile(path) as z:
        name = next(
            n for n in z.namelist()
            if n.endswith(".csv") and not n.startswith("__MACOSX")
        )
        with z.open(name) as fh:
            df = pd.read_csv(
                io.TextIOWrapper(fh, encoding="utf-8-sig"),
                usecols=["ride_id", "started_at", "ended_at", "member_casual"],
                dtype={"ride_id": "string", "member_casual": "category"},
            )
    df["started_at"] = pd.to_datetime(df["started_at"], format="mixed")
    df["ended_at"] = pd.to_datetime(df["ended_at"], format="mixed")
    df["src_file"] = ym[-2:]
    return df


def build_rides() -> tuple[pd.DataFrame, dict]:
    frames = [read_month(ym) for ym in MONTHS]
    trips = pd.concat(frames, ignore_index=True)

    qa: dict = {}
    qa["raw_rows"] = len(trips)
    qa["dup_ride_ids"] = int(trips["ride_id"].duplicated().sum())

    # Rides whose start month differs from the file that shipped them.
    trips["start_ym"] = trips["started_at"].dt.strftime("%m")
    trips["start_year"] = trips["started_at"].dt.year
    qa["file_month_mismatch"] = int((trips["start_ym"] != trips["src_file"]).sum())

    # Scope to CY2025 by LOCAL START DATE, which is what the brief asks for.
    outside = trips[trips["start_year"] != YEAR]
    qa["outside_cy"] = int(len(outside))
    qa["outside_detail"] = (
        outside.groupby(outside["started_at"].dt.date).size().to_dict()
    )
    trips = trips[trips["start_year"] == YEAR].copy()
    qa["cy_rows"] = len(trips)

    # Non-positive durations: flagged, counted, and left in. They are a
    # rounding artifact at this scale, and excluding them would change the
    # published ride count for no analytical gain. Documented, not silent.
    dur = (trips["ended_at"] - trips["started_at"]).dt.total_seconds()
    qa["nonpositive_duration"] = int((dur <= 0).sum())
    qa["duration_null"] = int(dur.isna().sum())

    trips["hour_local"] = trips["started_at"].dt.floor("h")
    g = trips.groupby("hour_local")
    rides = pd.DataFrame({
        "rides": g.size(),
        "member": g["member_casual"].apply(lambda s: (s == "member").sum()),
        "casual": g["member_casual"].apply(lambda s: (s == "casual").sum()),
    }).reset_index()

    qa["hour_buckets"] = len(rides)
    qa["days_covered"] = rides["hour_local"].dt.date.nunique()
    return rides, qa


# ----------------------------------------------------------------------------
# 2. Weather
# ----------------------------------------------------------------------------
def pull_weather() -> pd.DataFrame:
    """Open-Meteo ERA5 archive, hourly, requested in UTC.

    Why UTC and not timezone=America/Chicago:
    asking the API for a Chicago timezone returns a fixed -21600s offset and a
    uniform 24-labels-per-day grid, so every day has exactly 24 hours -
    including the two DST transition days, which cannot be true of real
    Chicago wall-clock time. Pulling UTC and converting with tz_convert
    reproduces local time by construction, including the missing 02:00 on
    2025-03-09 and the doubled 01:00 on 2025-11-02.

    The range is padded by a day on each side so that, after conversion, the
    local year is fully covered. A UTC-labelled calendar year converted to
    Chicago starts at 18:00 on Dec 31 of the previous year and ends at 17:00
    on Dec 31 - leaving the last six local hours of the year with no weather.
    """
    cache = DATA / "weather_hourly_utc.csv"
    if cache.exists():
        return pd.read_csv(cache)
    params = {
        "latitude": LAT,
        "longitude": LON,
        "start_date": f"{YEAR - 1}-12-30",
        "end_date": f"{YEAR + 1}-01-02",
        "hourly": ",".join(WEATHER_VARS),
        "timezone": "UTC",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "wind_speed_unit": "mph",
    }
    r = requests.get(METEO_URL, params=params, timeout=180)
    r.raise_for_status()
    j = r.json()
    wx = pd.DataFrame(j["hourly"]).rename(columns={"time": "time_utc"})
    # Record which grid point actually served the request - the API snaps the
    # requested coordinate to its nearest ERA5 cell, and that cell is what we
    # must cite, not the coordinate we asked for.
    wx.attrs["grid"] = {
        k: j.get(k) for k in
        ("latitude", "longitude", "elevation", "timezone", "utc_offset_seconds")
    }
    DATA.mkdir(parents=True, exist_ok=True)
    wx.to_csv(cache, index=False)
    (DATA / "weather_grid_point.txt").write_text(str(wx.attrs["grid"]) + "\n")
    return wx


def weather_to_local(wx: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    qa: dict = {}
    wx = wx.copy()
    wx["time_utc"] = pd.to_datetime(wx["time_utc"], utc=True, format="mixed")
    local = wx["time_utc"].dt.tz_convert("America/Chicago")
    wx["hour_local"] = local.dt.tz_localize(None)

    qa["rows_pulled"] = len(wx)
    qa["nulls"] = int(wx[WEATHER_VARS].isna().sum().sum())

    # DST diagnostics, asserted rather than trusted.
    dup = wx[wx["hour_local"].duplicated(keep=False)]
    qa["fallback_hours"] = dup["hour_local"].astype(str).unique().tolist()

    yr = wx[wx["hour_local"].dt.year == YEAR]
    full = pd.date_range(f"{YEAR}-01-01 00:00", f"{YEAR}-12-31 23:00", freq="h")
    missing = full.difference(pd.DatetimeIndex(yr["hour_local"].unique()))
    qa["springforward_missing"] = [str(t) for t in missing]

    # The doubled fall-back hour is collapsed to its mean so the join key is
    # unique. Divvy's started_at already lumps both real hours into one local
    # label, so averaging matches how the ride side is built.
    num = [c for c in WEATHER_VARS if pd.api.types.is_numeric_dtype(wx[c])]
    wxh = wx.groupby("hour_local", as_index=False)[num].mean()
    wxh = wxh[wxh["hour_local"].dt.year == YEAR].copy()
    qa["local_hours"] = len(wxh)
    return wxh, qa


# ----------------------------------------------------------------------------
# 3. Merge, verified
# ----------------------------------------------------------------------------
def merge_verified(rides: pd.DataFrame, wxh: pd.DataFrame) -> pd.DataFrame:
    m = rides.merge(wxh, on="hour_local", how="outer", indicator=True)

    log("=== MERGE VERIFICATION ===")
    counts = m["_merge"].value_counts()
    for k in ("both", "left_only", "right_only"):
        log(f"  {k:<11} {int(counts.get(k, 0)):>6}")

    only_rides = m[m["_merge"] == "left_only"]
    if len(only_rides):
        log("  !! ride-hours with NO weather (must be zero):")
        log(only_rides[["hour_local", "rides"]].to_string(index=False))
    else:
        log("  every ride-hour has weather. 0 unmatched.")

    only_wx = m[m["_merge"] == "right_only"]
    log(f"  weather hours with no rides: {len(only_wx)} "
        f"(genuine zero-ride hours, filled as 0 not NaN)")
    if len(only_wx):
        log(only_wx[["hour_local", "temperature_2m"]].head(5).to_string(index=False))

    m = m.drop(columns="_merge")
    for c in ("rides", "member", "casual"):
        m[c] = m[c].fillna(0).astype(int)
    m = m.sort_values("hour_local").reset_index(drop=True)

    assert m["temperature_2m"].isna().sum() == 0, "weather gaps survived the merge"
    assert len(only_rides) == 0, "unmatched ride-hours: merge is not trustworthy"
    return m


def add_calendar(df: pd.DataFrame, col: str) -> pd.DataFrame:
    t = pd.to_datetime(df[col])
    df["dow"] = t.dt.dayofweek
    df["dow_name"] = t.dt.day_name()
    df["month"] = t.dt.month
    df["is_weekend"] = df["dow"] >= 5
    return df


def to_daily(m: pd.DataFrame) -> pd.DataFrame:
    m = m.copy()
    m["date"] = m["hour_local"].dt.floor("D")
    agg = {
        "rides": "sum", "member": "sum", "casual": "sum",
        "temperature_2m": "mean", "apparent_temperature": "mean",
        "relative_humidity_2m": "mean", "precipitation": "sum",
        "rain": "sum", "snowfall": "sum", "snow_depth": "max",
        "wind_speed_10m": "mean", "wind_gusts_10m": "max", "cloud_cover": "mean",
    }
    d = m.groupby("date", as_index=False).agg(agg)
    d["temp_max"] = m.groupby("date")["temperature_2m"].max().to_numpy()
    d["temp_min"] = m.groupby("date")["temperature_2m"].min().to_numpy()
    d["apparent_max"] = m.groupby("date")["apparent_temperature"].max().to_numpy()
    d["hours_observed"] = m.groupby("date").size().to_numpy()
    d = add_calendar(d, "date")
    d["precip_any"] = d["precipitation"] > 0.01
    return d


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)

    log("=== DIVVY TRIPS ===")
    log(f"  months requested: {','.join(MONTHS)}")
    rides, qa = build_rides()
    log(f"  raw rows read               {qa['raw_rows']:>10,}")
    log(f"  duplicate ride_id           {qa['dup_ride_ids']:>10,}")
    log(f"  start month != file month   {qa['file_month_mismatch']:>10,}  "
        f"(keyed on started_at, so handled correctly)")
    log(f"  rides outside CY{YEAR}        {qa['outside_cy']:>10,}  "
        f"-> excluded: {qa['outside_detail']}")
    log(f"  rides in CY{YEAR}             {qa['cy_rows']:>10,}")
    log(f"  non-positive duration       {qa['nonpositive_duration']:>10,}  "
        f"(flagged, retained)")
    log(f"  null duration               {qa['duration_null']:>10,}")
    log(f"  local hour buckets          {qa['hour_buckets']:>10,}")
    log(f"  distinct local dates        {qa['days_covered']:>10,}")
    log("")

    log("=== WEATHER (Open-Meteo ERA5 archive) ===")
    wx = pull_weather()
    grid = (DATA / "weather_grid_point.txt")
    if grid.exists():
        log(f"  grid point served: {grid.read_text().strip()}")
    wxh, wqa = weather_to_local(wx)
    log(f"  rows pulled (UTC, padded)   {wqa['rows_pulled']:>10,}")
    log(f"  nulls across all variables  {wqa['nulls']:>10,}")
    log(f"  DST fall-back doubled hour  {wqa['fallback_hours']}")
    log(f"  DST spring-forward missing  {wqa['springforward_missing']}")
    log(f"  unique local {YEAR} hours     {wqa['local_hours']:>10,}")
    log("")

    m = merge_verified(rides, wxh)
    m = add_calendar(m, "hour_local")
    m["date"] = m["hour_local"].dt.date
    m["hour"] = m["hour_local"].dt.hour
    m.to_csv(DATA / "divvy_hourly_merged.csv", index=False)
    rides.to_csv(DATA / "divvy_hourly_local.csv", index=False)

    d = to_daily(m)
    d.to_csv(DATA / "divvy_daily_merged.csv", index=False)

    log("")
    log("=== OUTPUT ===")
    log(f"  hourly rows {len(m):,} | rides {int(m['rides'].sum()):,}")
    log(f"  daily rows  {len(d):,} | rides {int(d['rides'].sum()):,}")
    log(f"  days with zero rides: {int((d['rides'] == 0).sum())}")
    log(f"  days with <24 observed hours: "
        f"{d.loc[d['hours_observed'] != 24, ['date', 'hours_observed']].to_dict('records')}")
    assert int(m["rides"].sum()) == int(d["rides"].sum()), "daily rollup lost rides"
    assert int(d["rides"].sum()) == qa["cy_rows"], "daily total != CY ride count"
    log("  reconciliation: hourly == daily == CY2025 ride count. OK")

    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    mo = d.groupby("month")["rides"].sum()
    log("")
    log("=== MONTHLY TOTALS (by local start date) ===")
    for i, v in mo.items():
        log(f"  {names[i - 1]}  {v:>9,}")
    log(f"  {'TOTAL':<5}{mo.sum():>9,}")
    log(f"  peak month: {names[mo.idxmax() - 1]} ({mo.max():,})")

    (DATA / "merge_report.txt").write_text("\n".join(log_lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
