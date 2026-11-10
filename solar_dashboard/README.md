# FiT dashboard: solar

GB solar generation for the current scheme year (1 April to 31 March) against the previous
5 or 10 years, plus low, central and high weather scenarios.

## Run it

Needs Python 3.10 or later.

```
cd solar_dashboard
pip install -r requirements.txt
streamlit run app.py
```

The dashboard opens in your browser at http://localhost:8501.

## Update the data

`data/solar_daily.csv` holds daily GB solar generation (GWh) and installed capacity (MWp) from
Sheffield Solar's PV_Live, April 2016 to 9 November 2026 (2020/21 missing).

- **In the app:** click **Update from PV_Live** in the sidebar. It downloads any days missing since
  the last saved date. Your network must allow `api.pvlive.uk`.
- **By hand:** replace `data/solar_daily.csv` with a file that has the columns
  `date, generation_gwh, capacity_mwp`.

To add the missing 2020/21, run this once from the `solar_dashboard` folder:

```
python -c "import solar, datetime as d; print(solar.backfill(d.date(2020, 4, 1), d.date(2021, 4, 1)))"
```

## Files

- `app.py` – the Streamlit page: settings, charts and tables.
- `solar.py` – data loading, PV_Live download and all calculations (no Streamlit code, so you can
  reuse it in notebooks or other pages).
- `.streamlit/config.toml` – colours (SmartestEnergy teal) and font.

## Method

- **At today's capacity:** each day's load factor (GWh ÷ installed capacity × 24 h) × the latest
  installed capacity, so past years compare weather rather than the growth in panels.
- **Band:** min to max, or mean ± 2 standard deviations, of the comparison years on the same day.
  29 February is dropped so leap years line up.
- **Weather scenarios:** every comparison year's weather is replayed at today's capacity.
  - Current year = actual to date + each year's weather for the remaining days.
  - Next year = each year's full-year weather.
  - Low / central / high = P10 / P50 / P90, min / mean / max, or mean ± 1 standard deviation.
- **Weather factor:** scenario generation ÷ the comparison years' average for the same period.
  This is the number to use as the solar weather factor in the FiT cost model.
- PV_Live excludes solar sites in the Balancing Mechanism.
