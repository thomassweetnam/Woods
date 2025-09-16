# map_woodlands.py
import math
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin

import pandas as pd
import streamlit as st
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
import requests
from bs4 import BeautifulSoup

# ----- CONFIG -----
SITES_CSV = Path(r"C:\Users\thoma\OneDrive\Documents\Repositories\Glamping\woodlands_sites.csv")
CITY_CSV  = Path(r"C:\Users\thoma\OneDrive\Documents\Repositories\Glamping\gb.csv")  # optional
PDFS_DIR  = Path(r"C:\Users\thoma\OneDrive\Documents\Repositories\Glamping\PDFs")
UK_CENTER = (54.5, -3.0)
UK_ZOOM   = 5
DEFAULT_DISTANCE_MILES_THRESHOLD = 50.0
# -------------------

st.set_page_config(page_title="Woodlands Map", layout="wide")
st.title("Woodlands for Sale — Map view")

# ---------- Load sites ----------
if not SITES_CSV.exists():
    st.error(f"Sites CSV not found: {SITES_CSV}")
    st.stop()

sites = pd.read_csv(SITES_CSV, encoding="utf-8-sig")

# Defensive cleaning
for col in ["Latitude", "Longitude"]:
    if col in sites.columns:
        sites[col] = pd.to_numeric(sites[col], errors="coerce")

# --- Helpers for parsing numeric fields from text (fallbacks) ---
def parse_price_to_int(s):
    if pd.isna(s):
        return None
    digits = re.sub(r"[^\d]", "", str(s))
    return float(digits) if digits else None

FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1/3, "⅔": 2/3}
def parse_acres(text):
    if pd.isna(text):
        return None
    s = str(text).lower().replace("about", "").replace("approx", "")
    for k, v in FRACTIONS.items():
        s = s.replace(k, f"+{v}")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+\s*(0\.\d+))?", s)
    if not m:
        m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    if m:
        base = float(m.group(1))
        frac = float(m.group(2)) if m.lastindex and m.group(2) else 0.0
        return base + frac
    return None

# Numeric helpers for filters (and backward compatibility)
if "Price" in sites.columns and "Price_numeric" not in sites.columns:
    sites["Price_numeric"] = sites["Price"].apply(parse_price_to_int)

# Prefer your normalized SizeAcres; fall back to Acres_numeric made from Size text
if "SizeAcres" not in sites.columns:
    if "Acres_numeric" in sites.columns:
        sites["SizeAcres"] = sites["Acres_numeric"]
    elif "Size" in sites.columns:
        sites["SizeAcres"] = sites["Size"].apply(parse_acres)

# ---------- Sidebar: site filters ----------
st.sidebar.header("Filters — Woodlands")

# Type
types = sorted([t for t in sites.get("Type", []).dropna().unique()])
sel_types = st.sidebar.multiselect("Type", types, default=types)

# Price
if "Price_numeric" in sites and sites["Price_numeric"].notna().any():
    min_price = int(sites["Price_numeric"].dropna().min())
    max_price = int(sites["Price_numeric"].dropna().max())
    price_min, price_max = st.sidebar.slider("Price (£)", min_price, max_price, (min_price, max_price), step=1000)
else:
    price_min = price_max = None

# Size (acres) — from SizeAcres (preferred), else from Acres_numeric
size_acres_series = sites["SizeAcres"] if "SizeAcres" in sites.columns else sites.get("Acres_numeric")
if size_acres_series is not None and size_acres_series.notna().any():
    sa_min = float(size_acres_series.dropna().min())
    sa_max = float(size_acres_series.dropna().max())
    sel_sa_min, sel_sa_max = st.sidebar.slider("Size (acres)", sa_min, sa_max, (sa_min, sa_max))
else:
    sel_sa_min = sel_sa_max = None

# Size (m²) — optional, if you added Size_m2
if "Size_m2" in sites.columns and sites["Size_m2"].notna().any():
    sm2_min = float(sites["Size_m2"].dropna().min())
    sm2_max = float(sites["Size_m2"].dropna().max())
    sel_sm2_min, sel_sm2_max = st.sidebar.slider("Size (m²)", sm2_min, sm2_max, (sm2_min, sm2_max))
else:
    sel_sm2_min = sel_sm2_max = None

# √(m²) — optional, if you added Size_m2_sqrt
if "Size_m2_sqrt" in sites.columns and sites["Size_m2_sqrt"].notna().any():
    ss_min = float(sites["Size_m2_sqrt"].dropna().min())
    ss_max = float(sites["Size_m2_sqrt"].dropna().max())
    sel_ss_min, sel_ss_max = st.sidebar.slider("√(m²) ~ side length (m)", ss_min, ss_max, (ss_min, ss_max))
else:
    sel_ss_min = sel_ss_max = None

# Nearest City multiselect (if present)
nearest_city_options = sorted([c for c in sites.get("NearestCity", []).dropna().unique()])
sel_nearest_cities = st.sidebar.multiselect("Nearest City", nearest_city_options, default=nearest_city_options) if nearest_city_options else []

# Apply site-level filters
f_sites = sites.copy()
if sel_types:
    f_sites = f_sites[f_sites["Type"].isin(sel_types)]
if price_min is not None:
    f_sites = f_sites[(f_sites["Price_numeric"].isna()) | ((f_sites["Price_numeric"] >= price_min) & (f_sites["Price_numeric"] <= price_max))]
if sel_sa_min is not None:
    f_sites = f_sites[(f_sites["SizeAcres"].isna()) | ((f_sites["SizeAcres"] >= sel_sa_min) & (f_sites["SizeAcres"] <= sel_sa_max))]
if sel_sm2_min is not None:
    f_sites = f_sites[(f_sites["Size_m2"].isna()) | ((f_sites["Size_m2"] >= sel_sm2_min) & (f_sites["Size_m2"] <= sel_sm2_max))]
if sel_ss_min is not None:
    f_sites = f_sites[(f_sites["Size_m2_sqrt"].isna()) | ((f_sites["Size_m2_sqrt"] >= sel_ss_min) & (f_sites["Size_m2_sqrt"] <= sel_ss_max))]
if sel_nearest_cities:
    f_sites = f_sites[f_sites["NearestCity"].isin(sel_nearest_cities)]

f_sites = f_sites.dropna(subset=["Latitude", "Longitude"]).copy()

# ---------- Sidebar: city layer with population slider ----------
st.sidebar.header("Layers — Cities")
show_cities = st.sidebar.checkbox("Show UK cities", value=True)

city_df = None
if show_cities:
    if CITY_CSV.exists():
        city_df = pd.read_csv(CITY_CSV)
    else:
        st.sidebar.info("Upload a CSV of cities with columns: City (or Name), Latitude/lat, Longitude/lng, optional Population.")
        upload = st.sidebar.file_uploader("Upload gb.csv", type=["csv"])
        if upload is not None:
            city_df = pd.read_csv(upload)

# Normalize and filter cities
filtered_cities = None
if show_cities and city_df is not None and not city_df.empty:
    # Accept City/Name, Latitude/lat, Longitude/lng (case-insensitive)
    cols = {c.lower(): c for c in city_df.columns}
    name_col = cols.get("city") or cols.get("name")
    lat_col  = cols.get("latitude") or cols.get("lat")
    lon_col  = cols.get("longitude") or cols.get("lng")
    pop_col  = cols.get("population") or cols.get("pop")

    if name_col and lat_col and lon_col:
        city_df = city_df.rename(columns={
            name_col: "City",
            lat_col:  "Latitude",
            lon_col:  "Longitude"
        }).copy()

        if pop_col and pop_col != "Population":
            city_df.rename(columns={pop_col: "Population"}, inplace=True)

        city_df["Latitude"]  = pd.to_numeric(city_df["Latitude"], errors="coerce")
        city_df["Longitude"] = pd.to_numeric(city_df["Longitude"], errors="coerce")
        if "Population" in city_df.columns:
            city_df["Population"] = pd.to_numeric(city_df["Population"], errors="coerce")

        # ---- population slider (only if we have it) ----
        st.sidebar.subheader("City filters")
        if "Population" in city_df.columns and city_df["Population"].notna().any():
            pmin = int(city_df["Population"].dropna().min())
            pmax = int(city_df["Population"].dropna().max())
            sel_pmin, sel_pmax = st.sidebar.slider("City Population", pmin, pmax, (pmin, pmax), step=1000)
            filtered_cities = city_df[
                (city_df["Population"].isna()) |
                ((city_df["Population"] >= sel_pmin) & (city_df["Population"] <= sel_pmax))
            ].copy()
        else:
            st.sidebar.caption("No population column found — showing all cities.")
            filtered_cities = city_df.copy()

        filtered_cities = filtered_cities.dropna(subset=["Latitude", "Longitude"]).copy()
    else:
        st.warning("City CSV must have columns: City (or Name), Latitude/lat, Longitude/lng (Population optional).")
        filtered_cities = None

# ---------- Distance filtering to (filtered) cities ----------
st.sidebar.header("Distance to cities")
enable_distance_filter = st.sidebar.checkbox("Enable distance filter to nearest (filtered) city", value=False)
distance_threshold = st.sidebar.slider("Max distance to city (miles)", 5, 150, int(DEFAULT_DISTANCE_MILES_THRESHOLD), step=5)

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.7613  # miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)
    a = math.sin(dphi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2.0)**2
    return 2 * R * math.asin(math.sqrt(a))

def annotate_min_distance_to_cities(sites_df: pd.DataFrame, cities_df: pd.DataFrame) -> pd.DataFrame:
    """Adds MinCityMiles = min distance in miles to any (filtered) city."""
    if cities_df is None or cities_df.empty:
        sites_df = sites_df.copy()
        sites_df["MinCityMiles"] = None
        return sites_df

    cities_list = [(float(r["Latitude"]), float(r["Longitude"])) for _, r in cities_df.iterrows()]
    mins = []
    for _, row in sites_df.iterrows():
        lat_s, lon_s = float(row["Latitude"]), float(row["Longitude"])
        md = None
        for (clat, clon) in cities_list:
            d = haversine_miles(lat_s, lon_s, clat, clon)
            md = d if (md is None or d < md) else md
        mins.append(md)
    out = sites_df.copy()
    out["MinCityMiles"] = mins
    return out

if enable_distance_filter:
    if filtered_cities is None or filtered_cities.empty:
        st.warning("No (filtered) cities available — distance filter is disabled.")
    else:
        f_sites = annotate_min_distance_to_cities(f_sites, filtered_cities)
        before = len(f_sites)
        f_sites = f_sites[(f_sites["MinCityMiles"].notna()) & (f_sites["MinCityMiles"] <= distance_threshold)].copy()
        st.sidebar.caption(f"Filtered by distance: kept {len(f_sites)} of {before} woodlands (≤ {int(distance_threshold)} miles).")
else:
    if filtered_cities is not None and not filtered_cities.empty:
        # compute for display in popups/table
        f_sites = annotate_min_distance_to_cities(f_sites, filtered_cities)
    else:
        f_sites["MinCityMiles"] = None

# ---------- Map ----------
m = folium.Map(location=UK_CENTER, zoom_start=UK_ZOOM, control_scale=True, prefer_canvas=True)

# Woodlands layer
woodlands_group = folium.FeatureGroup(name="Woodlands", show=True).add_to(m)
woodland_cluster = MarkerCluster().add_to(woodlands_group)

def html_popup_site(row):
    name = row.get("Name", "Unknown")
    price = row.get("Price", "")
    typ = row.get("Type", "")
    size = row.get("Size", "")
    url = row.get("URL", "")
    dmi = row.get("MinCityMiles", None)
    dm_txt = f"<div>Nearest filtered city: {dmi:.1f} miles</div>" if pd.notna(dmi) else ""
    return f"""
    <div style="font-family: system-ui; font-size: 14px">
      <div style="font-weight: 600; font-size: 16px; margin-bottom: 4px;">{name}</div>
      <div>{price} &nbsp;•&nbsp; {typ}</div>
      <div>{size}</div>
      {dm_txt}
      <div style="margin-top:6px;"><a href="{url}" target="_blank" rel="noopener">Open listing ↗</a></div>
    </div>
    """

for _, row in f_sites.iterrows():
    lat, lon = float(row["Latitude"]), float(row["Longitude"])
    folium.Marker(
        location=(lat, lon),
        tooltip=row.get("Name", ""),
        popup=folium.Popup(html_popup_site(row), max_width=350),
        icon=folium.Icon(icon="tree-conifer", color="green"),
    ).add_to(woodland_cluster)

# Cities layer
if show_cities and filtered_cities is not None and not filtered_cities.empty:
    cities_group = folium.FeatureGroup(name="Cities", show=True).add_to(m)
    cities_cluster = MarkerCluster().add_to(cities_group)

    def html_popup_city(row):
        city = row.get("City", "City")
        pop  = row.get("Population", None)
        extra = f"<div>Population: {int(pop):,}</div>" if pd.notna(pop) else ""
        return f"""
        <div style="font-family: system-ui; font-size: 14px">
          <div style="font-weight: 600; font-size: 16px; margin-bottom: 4px;">{city}</div>
          {extra}
        </div>
        """

    for _, r in filtered_cities.iterrows():
        folium.Marker(
            location=(float(r["Latitude"]), float(r["Longitude"])),
            tooltip=str(r.get("City", "")),
            popup=folium.Popup(html_popup_city(r), max_width=300),
            icon=folium.Icon(icon="info-sign", color="blue"),
        ).add_to(cities_cluster)

# Layer control to toggle Woodlands / Cities
folium.LayerControl(collapsed=False).add_to(m)

sites_count  = len(f_sites)
cities_count = 0 if not (show_cities and filtered_cities is not None) else len(filtered_cities)
st.caption(f"Woodlands: {sites_count}  |  Cities (after population filter): {cities_count}")
st_data = st_folium(m, width=None, height=720)

# ---------- Tables ----------
with st.expander("Show woodlands table"):
    cols = [
        "Name", "Price", "Type", "Size",
        "SizeAcres", "Size_m2", "Size_m2_sqrt",
        "Latitude", "Longitude", "NearestCity", "MinCityMiles", "URL"
    ]
    existing_cols = [c for c in cols if c in f_sites.columns]
    st.dataframe(f_sites[existing_cols].reset_index(drop=True), use_container_width=True)

if show_cities and filtered_cities is not None and not filtered_cities.empty:
    with st.expander("Show cities table"):
        cols = ["City", "Latitude", "Longitude"] + (["Population"] if "Population" in filtered_cities.columns else [])
        st.dataframe(filtered_cities[cols].reset_index(drop=True), use_container_width=True)

# ================== PDF DOWNLOAD PANEL ==================
PDFS_DIR.mkdir(parents=True, exist_ok=True)

HEADERS_DL = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

def slug_from_url(u: str) -> str | None:
    try:
        path = urlparse(u).path.strip("/")
        return path.split("/")[-1] if path else None
    except Exception:
        return None

def sanitize_name_for_match(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s

def find_local_pdf(row) -> Path | None:
    """Try to locate a PDF in the PDFs folder for a given row."""
    slug = (slug_from_url(row.get("URL", "")) or "").lower()
    candidates = []
    if slug:
        for p in PDFS_DIR.glob("*.pdf"):
            if slug in p.stem.lower():
                candidates.append(p)

    if not candidates:
        name_key = sanitize_name_for_match(row.get("Name", ""))
        for p in PDFS_DIR.glob("*.pdf"):
            if name_key and name_key in p.stem.lower():
                candidates.append(p)

    if not candidates:
        return None
    candidates.sort(key=lambda p: len(p.name))
    return candidates[0]

def fetch_pdf_to_folder(detail_url: str) -> Path | None:
    """Visit the woodland page, find the 'Download PDF Details' link, download to PDFs folder."""
    try:
        r = requests.get(detail_url, headers=HEADERS_DL, timeout=30)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "html.parser")

        a = None
        for aa in s.select("a[href]"):
            if "download pdf details" in aa.get_text(strip=True).lower():
                a = aa
                break
        if not a:
            a = s.select_one('a[href$=".pdf"]')
        if not a:
            st.warning("Could not locate a PDF link on the page.")
            return None

        pdf_url = urljoin(detail_url, a["href"])
        pdf_name = Path(urlparse(pdf_url).path).name or "details.pdf"
        dest = PDFS_DIR / pdf_name

        rr = requests.get(pdf_url, headers=HEADERS_DL, timeout=60, stream=True)
        rr.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in rr.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return dest
    except Exception as e:
        st.error(f"PDF fetch failed: {e}")
        return None

# --------- CHOOSE SITE: map click OR dropdown ----------
st.subheader("Download listing PDF")

clicked = None
if st_data and st_data.get("last_object_clicked_popup"):
    # Extract site name from popup HTML
    popup_html = st_data["last_object_clicked_popup"]
    match = re.search(r'<div[^>]*>([^<]+)</div>', popup_html)
    if match:
        clicked_name = match.group(1).strip()
        row = f_sites[f_sites["Name"] == clicked_name]
        if not row.empty:
            clicked = row.iloc[0]

if clicked is not None:
    chosen_row = clicked
    st.write(f"**Selected from map:** {chosen_row['Name']}")
else:
    if f_sites.empty:
        st.info("No woodlands in the current filter.")
        st.stop()
    display_options = [f'{row["Name"]}  —  {row.get("Price","")}' for _, row in f_sites.iterrows()]
    choice = st.selectbox(
        "Select a woodland",
        options=list(range(len(display_options))),
        format_func=lambda i: display_options[i]
    )
    chosen_row = f_sites.iloc[choice]
    st.write(f"**Selected:** {chosen_row['Name']}")

# --------- Show download buttons for chosen_row ----------
local_pdf = find_local_pdf(chosen_row)

colA, colB = st.columns([1, 1])
with colA:
    if local_pdf and local_pdf.exists():
        with open(local_pdf, "rb") as f:
            data = f.read()
        st.download_button(
            label=f"Download PDF ({local_pdf.name})",
            data=data,
            file_name=local_pdf.name,
            mime="application/pdf",
            use_container_width=True
        )
        st.caption(f"Found local PDF in: {str(local_pdf)}")
    else:
        st.warning("No local PDF found for this site in your PDFs folder.")

with colB:
    if st.button("Fetch PDF into folder (if missing)", use_container_width=True):
        saved = fetch_pdf_to_folder(chosen_row.get("URL", ""))
        if saved and saved.exists():
            st.success(f"Saved: {saved.name}")
            with open(saved, "rb") as f:
                data2 = f.read()
            st.download_button(
                label=f"Download PDF ({saved.name})",
                data=data2,
                file_name=saved.name,
                mime="application/pdf",
                use_container_width=True
            )
        else:
            st.error("Could not fetch/save the PDF.")


# ================== BULK PDF DOWNLOAD (max 30) ==================
import io, zipfile, time
from datetime import datetime

st.subheader("Bulk download PDFs (max 30)")

if f_sites.empty:
    st.info("No woodlands in the current filter.")
else:
    # Choose how many (cap at 30)
    max_n = min(30, len(f_sites))
    n_to_fetch = st.number_input("How many PDFs (from top of filtered list)", min_value=1, max_value=max_n, value=max_n, step=1)

    # Optional: show which ones we are about to fetch (first n)
    with st.expander(f"Preview the first {int(n_to_fetch)} sites to download"):
        st.dataframe(f_sites.head(int(n_to_fetch))[["Name", "Price", "Type", "URL"]], use_container_width=True)

    if st.button(f"Fetch and bundle up to {int(n_to_fetch)} PDFs", use_container_width=True):
        subset = f_sites.head(int(n_to_fetch)).copy()

        progress = st.progress(0)
        status = st.empty()

        saved_paths = []
        skipped = 0
        errors = 0

        for idx, (_, row) in enumerate(subset.iterrows(), start=1):
            name = row.get("Name", f"site_{idx}")
            url  = row.get("URL", "")
            status.write(f"Processing {idx}/{int(n_to_fetch)}: **{name}**")

            try:
                # 1) try local
                p = find_local_pdf(row)
                if p is None or not p.exists():
                    # 2) fetch into folder
                    p = fetch_pdf_to_folder(url)
                if p and p.exists():
                    saved_paths.append(p)
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                st.warning(f"Failed on {name}: {e}")

            progress.progress(idx / float(n_to_fetch))
            # be polite to remote server if fetching
            time.sleep(0.2)

        if not saved_paths:
            st.error("No PDFs found or fetched.")
        else:
            # Build an in-memory ZIP
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # Avoid duplicate names inside the zip
                used_names = set()
                for p in saved_paths:
                    arcname = p.name
                    # If duplicate, add an index suffix
                    base, ext = Path(arcname).stem, Path(arcname).suffix
                    k = 1
                    while arcname in used_names:
                        arcname = f"{base}_{k}{ext}"
                        k += 1
                    used_names.add(arcname)
                    zf.write(p, arcname)
            buf.seek(0)

            zip_name = f"woodland_pdfs_{datetime.now():%Y%m%d_%H%M%S}.zip"
            st.success(f"Prepared {len(saved_paths)} PDFs (errors: {errors}).")
            st.download_button(
                label=f"Download ZIP ({zip_name})",
                data=buf.getvalue(),
                file_name=zip_name,
                mime="application/zip",
                use_container_width=True
            )
# ================================================================

# ======================================================= C:/Python313/python.exe -m streamlit run map_woodlands.py
