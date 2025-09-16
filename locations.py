import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# -------------- CONFIG --------------
BASE_URL = "https://www.woodlands.co.uk"
SEARCH_TPL = "https://www.woodlands.co.uk/buying-a-wood/search?location=HP11SW&page={page}"
OUT_CSV = Path(r"C:\Users\thoma\OneDrive\Documents\Repositories\Glamping\woodlands_sites.csv")
REQUEST_DELAY_SEC = 1
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}
# ------------------------------------

def get(url: str) -> requests.Response:
    print(f"[GET] {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp

def soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "html.parser")

def is_404(s: BeautifulSoup, status_code: int) -> bool:
    if status_code == 404:
        return True
    h1 = s.select_one("h1")
    return bool(h1 and "error 404" in h1.get_text(strip=True).lower())

def find_card_links(s: BeautifulSoup) -> list[str]:
    links = [urljoin(BASE_URL, a["href"]) for a in s.select("a.card__link[href]")]
    print(f"[INFO] Found {len(links)} woodland links on this page.")
    for link in links:
        print(f"       -> {link}")
    return links

def extract_name_price_type(s: BeautifulSoup) -> tuple[str, str, str]:
    """
    <section class="section section--short section--bg-yellow">
      <div class="section__inner">
        <div class="hero">
          <h1>
            Pottere Wood
            <span class="hero__extra">£59,000</span>
            <span class="hero__extra">Freehold</span>
          </h1>
        </div>
      </div>
    </section>
    """
    # Prefer the exact hero H1; fall back to the first H1 on the page
    h1 = s.select_one("section.section--bg-yellow h1") or s.select_one("h1")
    if not h1:
        raise RuntimeError("h1 not found for name/price/type")

    # Pull spans explicitly
    spans = h1.select("span.hero__extra")
    price = spans[0].get_text(strip=True) if len(spans) >= 1 else ""
    wtype = spans[1].get_text(strip=True) if len(spans) >= 2 else ""

    # Remove spans from a copy, then read the clean name
    h1_copy = BeautifulSoup(str(h1), "html.parser").h1
    for sp in h1_copy.find_all("span"):
        sp.decompose()
    name = h1_copy.get_text(strip=True)

    print(f"[PARSE] Name: {name} | Price: {price} | Type: {wtype}")
    return name, price, wtype

def extract_size(s: BeautifulSoup) -> str:
    """
    Second <li> in the header details row usually contains the size (e.g., 'about 2 ½ acres').
    Fallback: any <li> with 'acres' in it.
    """
    # Try header metadata list under the title section
    for ul in s.select("section ul"):
        lis = ul.find_all("li")
        if len(lis) >= 2 and "acres" in lis[1].get_text(" ", strip=True).lower():
            size = lis[1].get_text(" ", strip=True)
            print(f"[PARSE] Size: {size}")
            return size
    # Fallback
    li = s.find("li", string=lambda t: isinstance(t, str) and "acres" in t.lower())
    size = li.get_text(" ", strip=True) if li else ""
    print(f"[PARSE] Size (fallback): {size}")
    return size

def extract_gps(s: BeautifulSoup) -> tuple[str, float, float]:
    """
    Find 'GPS coordinates: 51.7061, -0.240244' and return text + floats.
    """
    gps_text = ""
    for li in s.select("li"):
        t = li.get_text(" ", strip=True)
        if t.lower().startswith("gps coordinates"):
            gps_text = t
            break
    search_space = gps_text or s.get_text(" ", strip=True)
    m = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", search_space)
    lat = lon = None
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    print(f"[PARSE] GPS: {gps_text} -> lat={lat}, lon={lon}")
    return gps_text, lat, lon

def scrape_detail(detail_url: str) -> dict:
    r = get(detail_url)
    r.raise_for_status()
    s = soup(r)

    name, price, wtype = extract_name_price_type(s)
    size = extract_size(s)
    gps_text, lat, lon = extract_gps(s)

    return {
        "Name": name,
        "Price": price,
        "Type": wtype,
        "Size": size,
        "Latitude": lat,
        "Longitude": lon,
        "GPS_Text": gps_text,
        "URL": detail_url,
    }

def main():
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    page = 1
    seen = set()

    while True:
        search_url = SEARCH_TPL.format(page=page)
        resp = get(search_url)
        s = soup(resp)

        if is_404(s, resp.status_code):
            print(f"[STOP] Page {page} is 404/end of results.")
            break

        detail_links = find_card_links(s)
        if not detail_links:
            print(f"[STOP] No cards on page {page}.")
            break

        for i, link in enumerate(detail_links, start=1):
            if link in seen:
                print(f"[SKIP] Already scraped: {link}")
                continue
            seen.add(link)

            print(f"\n=== Page {page} — Woodland {i}/{len(detail_links)} ===")
            try:
                row = scrape_detail(link)
                print(f"[OK] {row['Name']} | {row['Price']} | {row['Type']} | {row['Size']}")
                rows.append(row)
            except Exception as e:
                print(f"[ERROR] Failed to scrape {link}: {e}")
            finally:
                time.sleep(REQUEST_DELAY_SEC)

        page += 1
        print(f"\n[PAGE] Moving to page {page}...")
        time.sleep(REQUEST_DELAY_SEC)

    # Write CSV (UTF-8 BOM for Excel-friendly ½)
    fieldnames = ["Name", "Price", "Type", "Size", "Latitude", "Longitude", "GPS_Text", "URL"]
    print(f"\n[WRITE] Saving {len(rows)} rows to: {OUT_CSV}")
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print("[DONE] CSV saved.")

if __name__ == "__main__":
    main()
