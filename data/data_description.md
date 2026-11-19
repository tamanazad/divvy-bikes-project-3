# Data description — DSDA 310 Project 3

Everything here is produced by `build_dataset.py` from source. Nothing is edited
by hand. Re-running the script regenerates every file below and rewrites
`merge_report.txt`.

---

## Target

**`divvy_daily_merged.csv` — 365 rows, one per local Chicago calendar date.**

`rides` is the total count of completed Divvy trips whose **`started_at`** falls
on that local date. This is the quantity the operations manager needs: how much
fleet has to be in the street tomorrow.

---

## Source 1 — Divvy trip records

| | |
|---|---|
| Source | `https://divvy-tripdata.s3.amazonaws.com/` |
| Files | twelve monthly archives, `202501` … `202512` |
| Scope | calendar year 2025 |
| Grain in | one row per trip |
| Grain out | rides per local hour → rides per local date |
| Date field | `started_at` (local Chicago wall-clock, millisecond precision) |
| Units | counts of trips |

### Decisions that changed the numbers

| Decision | Effect |
|---|---|
| Rides keyed on `started_at`, not on the file they shipped in | 1,075 rides sit in a file whose month differs from their start month; keying on the timestamp places each one in the right month |
| Rides starting 2024-12-31 excluded | 53 rides in the January archive; 5,552,994 raw − 53 = **5,552,941** in scope |
| `ended_at <= started_at` retained | 29 rides. Flagged and counted, not dropped — immaterial at this scale, and dropping them would change the published ride count for no analytical gain |
| Blank station name / id not repaired | ~33.5 % of rows (dockless e-bikes). Station fields are not used in the daily or hourly target. **If we cluster on station profiles, this subset needs a documented rule first.** |
| Trip duration not used as a filter | no minimum-duration or round-trip exclusion applied |

### Coverage

All 365 days of 2025 are present and **no day has zero rides**. 8,758 of the
8,759 local hours in 2025 contain at least one ride; the exception is
2025-01-22 03:00 (0.7 °F), a genuine zero rather than a missing observation, and
it is filled as 0.

### Parsing gotcha

`start_lat`, `start_lng`, `end_lat`, `end_lng` are **unquoted** while every other
field is quoted, and line endings are CRLF. A hand-rolled split on `","` fails
silently on every row. Use a real CSV parser.

---

## Source 2 — Weather

| | |
|---|---|
| Source | Open-Meteo **ERA5 archive**, `https://archive-api.open-meteo.com/v1/archive` |
| Requested point | 41.8781 N, −87.6298 W (Chicago, from the assignment) |
| **Grid point actually served** | **41.8629 N, −87.6488 W, elevation 241 m** — recorded from the API response at pull time (17 Nov 2026) and kept in `weather_grid_point.txt`. The API snaps the requested coordinate to its nearest ERA5 cell, and that cell is what we cite. Note the deprecated `.xlsx` export reports the same coordinate at elevation 183 m, so the two were served by different Open-Meteo products — another reason not to mix them. |
| Grain | hourly, aggregated to daily |
| Date field | `time`, requested in **UTC** |
| Temperature | **°F** (`temperature_unit=fahrenheit`) |
| Precipitation | **inches** (`precipitation_unit=inch`) |
| Wind | **mph** (`wind_speed_unit=mph`) |
| Humidity / cloud | per cent |
| Gaps | **none** — zero nulls across all twelve variables for the full window |

Variables pulled: `temperature_2m`, `apparent_temperature`,
`relative_humidity_2m`, `precipitation`, `rain`, `snowfall`, `snow_depth`,
`wind_speed_10m`, `wind_gusts_10m`, `cloud_cover`, `weather_code`, `is_day`.

ERA5 is a reanalysis on a roughly 9 km grid, not a gauge reading at a station.
It is internally consistent but will not match any specific Chicago observation
exactly.

Open-Meteo asks that research cite it and its underlying datasets (ERA5,
ERA5-Land, CERRA).

---

## The timezone decision — read this before changing the merge

Divvy's `started_at` is **local Chicago wall-clock time**. Weather must be put on
that same clock before it can be joined.

**We pull in UTC and convert with `tz_convert("America/Chicago")`.** We do *not*
pass `timezone=America/Chicago` to the API: that returns a fixed
`utc_offset_seconds: -21600` and a uniform 24-labels-per-day grid, so every day
has exactly 24 hours — including the two DST transition days, which cannot be
true of real Chicago wall-clock time.

Converting from UTC reproduces local time by construction, and the script
asserts both transitions:

* **2025-03-09 02:00 does not exist** (spring forward). Divvy correctly logs no
  rides in that hour, and that date has 23 observed hours.
* **2025-11-02 01:00 occurs twice** (fall back). The two readings are collapsed
  to their mean so the join key is unique — which matches the ride side, where
  `started_at` already labels both real hours as 01:00.

The request range is padded a day either side. Without the pad, a UTC-labelled
calendar year converts to local 2024-12-31 18:00 → 2025-12-31 17:00, leaving the
**last six local hours of the year with no weather**.

---

## ⚠ `open-meteo_chicago.xlsx` — DEPRECATED, do not merge against it

This file is kept **only** as the audit exhibit for section 4 of
`02_eda_milestone.ipynb`. It must not be used to build any dataset.

Its header block reads `timezone: GMT`, `utc_offset_seconds: 0` — the export is
in **GMT**, not Chicago local time. Joining it directly onto `started_at`, as
`dataframe_merger.ipynb` did, attaches each ride the weather from five or six
hours earlier:

* the join matches **100 % of rows and raises nothing**, so row counts and null
  counts do not detect the error;
* mean absolute error in the attached temperature is **2.58 °C**, worst hour
  **14.5 °C**, and **12.6 %** of ride-hours are wrong by more than 5 °C;
* hourly correlation between temperature and rides falls from **+0.585** to
  **+0.497** — real signal is destroyed;
* it also covers only four variables, in °C / mm / km/h, and its local coverage
  stops at 2025-12-31 17:00.

---

## Merge verification

`build_dataset.py` merges with `indicator=True` and **asserts zero unmatched
ride-hours**, so a future re-run fails loudly rather than silently:

```
both          8758
left_only        0     <- ride-hours with no weather. Must be zero.
right_only       1     <- 2025-01-22 03:00, a genuine zero-ride hour
```

It then asserts that the hourly total, the daily total and the in-scope CY2025
ride count are all equal.

---

## Files

| File | Grain | Rows | What it is |
|---|---|---|---|
| `divvy_daily_merged.csv` | local date | 365 | **the forecasting target**, rides + daily weather |
| `divvy_hourly_merged.csv` | local hour | 8,759 | rides + hourly weather |
| `divvy_hourly_local.csv` | local hour | 8,758 | rides only, before the weather join |
| `weather_hourly_utc.csv` | UTC hour | 8,808 | weather exactly as pulled, cached |
| `weather_grid_point.txt` | — | — | the ERA5 cell the API actually served |
| `merge_report.txt` | — | — | the verification log from the last run |
| `raw/*.zip` | — | 12 | cached Divvy archives (gitignored) |
| `open-meteo_chicago.xlsx` | — | — | **deprecated**, audit exhibit only |

### Column notes for `divvy_daily_merged.csv`

`rides`, `member`, `casual` are counts. `temperature_2m`,
`apparent_temperature`, `relative_humidity_2m`, `wind_speed_10m`, `cloud_cover`
are daily **means**; `precipitation`, `rain`, `snowfall` are daily **sums**;
`snow_depth`, `wind_gusts_10m`, `temp_max`, `apparent_max` are daily **maxima**;
`temp_min` is the daily minimum. `hours_observed` is 24 on every date except
2025-03-09, which has 23 — correctly, because that local hour does not exist.
`precip_any` is `precipitation > 0.01` inches.
