"""
FiT dashboard: GB solar generation against previous years, and weather scenarios.

Run:  streamlit run app.py
"""
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import solar

TEAL = "#005763"
BLUE = "#00DED4"
GREY = "#CFD2D3"
ASH = "#656F7A"

st.set_page_config(page_title="FiT dashboard: solar", layout="wide")


@st.cache_data(show_spinner=False)
def load(_mtime: float) -> pd.DataFrame:
    return solar.load_daily()


def data_mtime() -> float:
    return solar.DATA_FILE.stat().st_mtime


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Solar settings")
    lookback = st.radio("Compare with", [10, 5], format_func=lambda n: f"Previous {n} years", horizontal=True)
    band = st.radio("Band", ["Min to max", "Mean ± 2 standard deviations"])
    measure = st.radio("Measure", ["At today’s capacity", "Actual GWh"],
                       help="“At today’s capacity” rescales each past year’s load factor to today’s "
                            "installed capacity, so the band shows weather rather than growth in panels.")
    smooth = st.radio("Smoothing", ["7-day average", "Daily"], horizontal=True)
    st.divider()
    st.subheader("Data")
    if st.button("Update from PV_Live", help="Downloads any days missing since the last saved date."):
        try:
            with st.spinner("Downloading from PV_Live…"):
                added = solar.update_from_pvlive()
            st.success(f"Added {added} days." if added else "Already up to date.")
            st.cache_data.clear()
        except requests.RequestException as e:
            st.error("Couldn’t reach api.pvlive.uk. Check your network or proxy allows it, "
                     f"or download the CSV in a browser. ({e.__class__.__name__})")

if not solar.DATA_FILE.exists():
    st.error(
        "The solar data file is missing. The app looks for `data/solar_daily.csv` (or "
        "`solar_daily.csv` next to `app.py`). Add it to the repository, or download the data now. "
        "On Streamlit Cloud a downloaded file is lost when the app restarts, so committing the "
        "file to GitHub is the lasting fix."
    )
    if st.button("Download from PV_Live now (takes a few minutes)"):
        try:
            with st.spinner("Downloading April 2016 to yesterday from PV_Live…"):
                n = solar.create_from_pvlive()
            st.success(f"Downloaded {n} days.")
            st.cache_data.clear()
            st.rerun()
        except requests.RequestException as e:
            st.error(f"Couldn’t reach api.pvlive.uk ({e.__class__.__name__}).")
    st.stop()

df = load(data_mtime())
measure_key = "adjusted" if measure.startswith("At") else "actual"
smooth_n = 7 if smooth.startswith("7") else 1
b = solar.bands(df, lookback, measure_key, smooth_n)
fy_label = solar.label(b["current_fy"])

with st.sidebar:
    st.caption(f"Sheffield Solar PV_Live, {df['date'].min():%d %b %Y} to {df['date'].max():%d %b %Y}. "
               f"Comparing {len(b['years'])} years: {', '.join(solar.label(y) for y in b['years'])}.")

# ---------------------------------------------------------------- header

st.title(f"GB solar generation, {fy_label}")
s = solar.ytd_summary(b)
if s:
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Solar since 1 April to {b['last_date'].day} {b['last_date']:%b}", f"{s['ytd_gwh']:,.0f} GWh")
    c2.metric("Against the historic average", f"{s['vs_mean_pct']:+.1f}%",
              help="Cumulative generation since 1 April against the average of the comparison years for the same days.")
    c3.metric("Position in historic range", f"{s['range_position_pct']:.0f}%",
              help="0% = the lowest comparison year to date, 100% = the highest. Outside 0–100% is a new low or high.")


# ---------------------------------------------------------------- band chart

def band_chart(frame: pd.DataFrame, title: str, y_title: str) -> go.Figure:
    lo, hi = ("lower_2sd", "upper_2sd") if band.startswith("Mean") else ("min", "max")
    band_name = "Mean ± 2 sd" if band.startswith("Mean") else "Historic min to max"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=frame["day"], y=frame[hi], mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=frame["day"], y=frame[lo], mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(0,222,212,0.22)", name=band_name,
                             customdata=frame[[hi, "date_label"]],
                             hovertemplate="%{customdata[1]}<br>Band %{y:,.1f} to %{customdata[0]:,.1f} GWh<extra></extra>"))
    fig.add_trace(go.Scatter(x=frame["day"], y=frame["mean"], mode="lines", name=f"Historic mean ({len(b['years'])} years)",
                             line=dict(color=TEAL, width=1.5, dash="dash"), customdata=frame["date_label"],
                             hovertemplate="%{customdata}<br>Mean %{y:,.1f} GWh<extra></extra>"))
    fig.add_trace(go.Scatter(x=frame["day"], y=frame["current"], mode="lines", name=fy_label,
                             line=dict(color=TEAL, width=2.5), customdata=frame["date_label"],
                             hovertemplate="%{customdata}<br>" + fy_label + " %{y:,.1f} GWh<extra></extra>"))
    fig.update_layout(
        title=dict(text=title, font=dict(size=16)), height=380, margin=dict(l=10, r=10, t=50, b=10),
        font=dict(family="Arial, Helvetica, sans-serif"), hovermode="x unified", plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        xaxis=dict(tickvals=[d for d, _ in solar.MONTH_TICKS], ticktext=[m for _, m in solar.MONTH_TICKS],
                   range=[0, 364], showgrid=False, linecolor=GREY),
        yaxis=dict(title=y_title, gridcolor="#EEF0F1", rangemode="tozero"),
    )
    return fig


st.plotly_chart(band_chart(b["daily"], "Daily generation", "GWh per day"), width="stretch")
st.plotly_chart(band_chart(b["cumulative"], "Cumulative generation from 1 April", "GWh"), width="stretch")
st.caption("Grid-connected PV from Sheffield Solar’s PV_Live, which excludes solar sites in the Balancing Mechanism. "
           "29 February is dropped so leap years line up.")

# ---------------------------------------------------------------- weather scenarios

st.header("Weather scenarios")
cc1, cc2 = st.columns([2, 1])
method = cc1.radio("Low / central / high", list(solar.SCENARIO_METHODS), horizontal=True,
                   help="P10/P50/P90 take the 10th, 50th and 90th percentiles of the comparison years.")
growth = cc2.number_input("Capacity growth next year (%)", value=0.0, step=1.0,
                          help="GB solar capacity keeps growing; FiT capacity does not. Leave at 0% "
                               "to read the weather factor for the FiT model.")

sc = solar.weather_scenarios(df, lookback, method, growth)
st.write(
    f"Every comparison year’s weather replayed at today’s installed capacity "
    f"({sc.attrs['capacity_mwp']:,.0f} MWp). {fy_label} adds each year’s weather for the remaining "
    f"{sc.attrs['rest_days']} days to the {sc.attrs['ytd_gwh']:,.0f} GWh generated to "
    f"{sc.attrs['last_date'].day} {sc.attrs['last_date']:%B}."
)
rows_fmt = {r: ("{:,.0f}" if "GWh" in r else "{:+.1f}%" if "vs average" in r else "{:.3f}" if "weather factor" in r
                 else "{:.1f}%" if "load factor" in r else "{}") for r in sc.columns}
tbl = sc.T.copy()
tbl.attrs = {}
tbl = tbl.apply(lambda col: [rows_fmt[r].format(v) for r, v in zip(tbl.index, col)])
tbl.index.name = "Measure"
st.dataframe(tbl, width="stretch")
st.caption("Weather factor = scenario generation ÷ the comparison years’ average for the same period. "
           "Use it as the solar weather factor in the FiT cost model.")

with st.expander("Each comparison year"):
    yy = solar.year_by_year(df, lookback)
    st.dataframe(yy.style.format({"Full year at today's capacity (GWh)": "{:,.0f}", "Weather factor": "{:.3f}",
                                  "Rest-of-year weather factor": "{:.3f}"}),
                 width="stretch", hide_index=True)
    st.download_button("Download scenarios as CSV", sc.to_csv().encode(), "solar_weather_scenarios.csv", "text/csv")
