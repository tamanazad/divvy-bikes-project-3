# Divvy Ridership Forecasting — DSDA 310 Project 3

Seven-day forecast of daily citywide Chicago Divvy ridership, built from calendar
year 2025 trip records and daily weather.

**Team:** Taman Azad, Isaac Guttormson
**Showcase:** Thursday 3 December 2026 · **Repo due:** Tuesday 2 December 2026

---

## The decision this serves

An operations manager deciding **how many bikes to stage tomorrow morning**. The
target is therefore total completed rides per local calendar day, citywide —
the quantity that determines how much fleet has to be in the street. Every
modelling choice traces back to that.

---

## Run it from a clean clone

```bash
git clone https://github.com/tamanazad/divvy-bikes-project-3
cd divvy-bikes-project-3
pip install -r requirements.txt
python build_dataset.py          # ~2 min: downloads, aggregates, merges, verifies
jupyter lab 02_eda_milestone.ipynb
```

`build_dataset.py` needs no API key and no manual download. It fetches the twelve
2025 Divvy archives (~204 MB, cached in `data/raw/` and gitignored) and the
Open-Meteo ERA5 weather, then writes the modelling tables plus
`data/merge_report.txt`. Re-running it is safe — cached inputs are reused.

`data/weather_hourly_utc.csv` is committed, so a clean clone does **not** call
the weather API at all. It is only re-fetched if you delete that file — and the
Open-Meteo archive endpoint enforces a daily request quota per IP, so if you do
delete it you may get `Daily API request limit exceeded` and have to wait. Keep
the cached file unless you have a reason to re-pull.

A partial run for testing: `MONTHS=02,03 python build_dataset.py`.

---

## What is here

| Path | What it is |
|---|---|
| `build_dataset.py` | the whole pipeline: download → aggregate → merge → verify |
| `02_eda_milestone.ipynb` | the 19 Nov milestone: daily table, seasonal charts, merge verification, data quality, baseline plan |
| `data/data_description.md` | sources, units, exclusions, timezone decision, column notes |
| `data/divvy_daily_merged.csv` | **the forecasting target** — 365 rows |
| `data/divvy_hourly_merged.csv` | hourly grain, 8,759 rows |
| `data/merge_report.txt` | verification log from the last run |
| `dataframe_merger.ipynb` | superseded — see below |

---

## Headline numbers

| | |
|---|---|
| Rides in scope, CY2025 | **5,552,941** |
| Peak month | August, 790,317 (14.2 % of the year) |
| Trough month | January, 138,651 |
| Seasonal swing | **5.70×** |
| Busiest / quietest day | 2 Aug, 32,308 · 29 Nov, 1,024 (snowstorm) |
| Member / casual | 64.0 % / 36.0 % |
| Best single weather predictor | apparent temperature, r = **+0.905** |
| Baseline to beat | **MAE 2,538** (lag-1), *not* 3,245 (lag-7) |

---

## Two things that will bite anyone who touches this pipeline

**1. The weather must be pulled in UTC and converted, never requested in local
time.** Asking Open-Meteo for `timezone=America/Chicago` returns a uniform
24-hours-per-day grid that cannot represent either DST transition. We pull UTC
and `tz_convert`, which reproduces local time by construction — including the
missing 2025-03-09 02:00 and the doubled 2025-11-02 01:00.

**2. `data/open-meteo_chicago.xlsx` is in GMT and is deprecated.** It is kept
only as the audit exhibit in section 4 of the notebook.
`dataframe_merger.ipynb` joined it straight onto Divvy's local timestamps; that
join matches 100 % of rows and raises nothing, while attaching each ride the
weather from five or six hours earlier — 2.58 °C mean absolute error, 12.6 % of
ride-hours wrong by more than 5 °C, and hourly temperature-vs-rides correlation
cut from +0.585 to +0.497. `build_dataset.py` supersedes it and asserts zero
unmatched ride-hours so the failure can never be silent again.

---

## Status

- [x] CY2025 data acquired and reconciled (5,552,941 rides)
- [x] Daily ridership table
- [x] Seasonal visualisation
- [x] Weather merge **verified**
- [x] Data-quality summary
- [x] Baseline plan
- [ ] Clustering — unit of analysis still to be fixed
- [ ] Forecasting model and chronological evaluation (24 Nov)
- [ ] Dashboard (1 Dec)
- [ ] Executive memo, 3 pages (2 Dec)

---

## Data sources

Divvy system data, `https://divvy-tripdata.s3.amazonaws.com/`, under the
[Divvy Data License Agreement](https://divvybikes.com/data-license-agreement).

Weather from the [Open-Meteo](https://open-meteo.com/) ERA5 archive
(Copernicus Climate Change Service).
