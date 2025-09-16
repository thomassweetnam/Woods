import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# ---------- CONFIG ----------
BASE_URL = "https://www.woodlands.co.uk"
SEARCH_TEMPLATE = (
    "https://www.woodlands.co.uk/buying-a-wood/search?location=HP11SW&page={page}"
)
SAVE_DIR = Path(r"C:\Users\thoma\OneDrive\Documents\Repositories\Glamping")
REQUEST_DELAY_SEC = 1  # be polite
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}
# ----------------------------

def get_response(url: str) -> requests.Response:
    print(f"[GET] {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp

def soup_of(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "html.parser")

def page_is_404(soup: BeautifulSoup, status: int) -> bool:
    """Detect the 404 page either by status code or by the H1 text you showed."""
    if status == 404:
        return True
    h1 = soup.select_one("h1")
    if h1 and "error 404" in h1.get_text(strip=True).lower():
        return True
    return False

def find_all_card_links(soup: BeautifulSoup):
    links = [urljoin(BASE_URL, a["href"]) for a in soup.select("a.card__link[href]")]
    print(f"[INFO] Found {len(links)} woodland card links on this page.")
    for link in links:
        print(f"       -> {link}")
    return links

def find_pdf_link_on_detail_page(soup: BeautifulSoup) -> str:
    # Prefer the explicit button text
    for a in soup.select('a[href]'):
        if "download pdf details" in a.get_text(strip=True).lower():
            return urljoin(BASE_URL, a["href"])
    # Fallback: any .pdf link
    a = soup.select_one('a[href$=".pdf"]')
    if a:
        return urljoin(BASE_URL, a["href"])
    raise RuntimeError("No PDF link found on detail page.")

def download_file(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(url).path).name or "details.pdf"
    dest = dest_dir / name
    if dest.exists():
        print(f"[SKIP] Already exists: {dest}")
        return dest

    print(f"[DOWNLOAD] {url} -> {dest}")
    with requests.get(url, headers=HEADERS, timeout=60, stream=True) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    size += len(chunk)
    print(f"[SAVED] {dest} ({size} bytes)")
    return dest

def main():
    seen_detail_pages = set()
    page = 1
    total_downloads = 0

    while True:
        search_url = SEARCH_TEMPLATE.format(page=page)
        resp = get_response(search_url)
        soup = soup_of(resp)

        # Stop when we hit the 404 page
        if page_is_404(soup, resp.status_code):
            print(f"[STOP] Page {page} appears to be 404 / end of results. Finishing.")
            break

        try:
            detail_links = find_all_card_links(soup)
        except Exception as e:
            print(f"[WARN] Could not parse links on page {page}: {e}")
            break

        if not detail_links:
            print(f"[STOP] No woodland cards found on page {page}.")
            break

        for i, detail_url in enumerate(detail_links, start=1):
            if detail_url in seen_detail_pages:
                print(f"[SKIP] Already processed: {detail_url}")
                continue
            seen_detail_pages.add(detail_url)

            print(f"\n=== Page {page} — Woodland {i}/{len(detail_links)} ===")
            try:
                d_resp = get_response(detail_url)
                d_resp.raise_for_status()
                d_soup = soup_of(d_resp)
                pdf_url = find_pdf_link_on_detail_page(d_soup)
                print(f"[PDF] {pdf_url}")
                download_file(pdf_url, SAVE_DIR)
                total_downloads += 1
            except Exception as e:
                print(f"[ERROR] Failed on {detail_url}: {e}")
            finally:
                time.sleep(REQUEST_DELAY_SEC)

        page += 1
        print(f"\n[PAGE] Moving to page {page}...\n")
        time.sleep(REQUEST_DELAY_SEC)

    print(f"\n[DONE] Finished. PDFs downloaded: {total_downloads}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] User stopped the script.")
        sys.exit(1)
    except Exception as e:
        print("[FATAL]", e)
        sys.exit(1)
