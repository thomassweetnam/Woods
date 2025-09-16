import re
import argparse
import math
from pathlib import Path
from datetime import datetime
import pandas as pd

FRACTIONS = {
    "½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1/3, "⅔": 2/3,
}

ACRE_TO_M2 = 4046.8564224  # 1 acre in square metres

def parse_acres(text: str | float) -> float | None:
    """Turn 'about 2 ½ acres' or '1 3/4 acres' into numeric acres."""
    if pd.isna(text):
        return None
    s = str(text).lower()
    # remove fluff words
    s = re.sub(r"\b(about|approx(?:imately)?|over|just over|c\.)\b", "", s)
    s = s.replace("acres", "").replace("acre", "").strip()

    # vulgar fractions → +decimal
    for sym, dec in FRACTIONS.items():
        s = s.replace(sym, f" +{dec}")

    # ascii mixed fraction "1 3/4"
    m = re.search(r"(\d+)\s+(\d+)\s*/\s*(\d+)", s)
    if m:
        whole = float(m.group(1))
        num = float(m.group(2))
        den = float(m.group(3)) if float(m.group(3)) != 0 else 1.0
        return whole + (num/den)

    # "a +0.5" pattern
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+\s*(0?\.\d+))?", s)
    if m:
        base = float(m.group(1))
        frac = float(m.group(2)) if m.lastindex and m.group(2) else 0.0
        return base + frac

    # simple float fallback
    m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None

def main():
    ap = argparse.ArgumentParser(description="Normalize Size to numeric acres, m², and sqrt(m²).")
    ap.add_argument(
        "csv",
        nargs="?",
        default=r"C:\Users\thoma\OneDrive\Documents\Repositories\Glamping\woodlands_sites.csv",
        help="Path to woodlands CSV (default: your file).",
    )
    args = ap.parse_args()
    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig")

    if "Size" not in df.columns:
        raise SystemExit("Column 'Size' not found in CSV.")

    # backup
    backup = path.with_name(path.stem + f".backup_{datetime.now():%Y%m%d_%H%M%S}" + path.suffix)
    df.to_csv(backup, index=False, encoding="utf-8-sig")
    print(f"[backup] {backup}")

    # normalize
    df["SizeAcres"] = df["Size"].apply(parse_acres)
    df["Size_m2"] = df["SizeAcres"] * ACRE_TO_M2
    df["Size_m2_sqrt"] = df["Size_m2"].apply(lambda x: math.sqrt(x) if pd.notna(x) else None)

    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[done]   updated {path}")
    print(df[["Name", "Size", "SizeAcres", "Size_m2", "Size_m2_sqrt"]].head())

if __name__ == "__main__":
    main()
