import pandas as pd
import requests
from io import StringIO
from pathlib import Path
import time

# --------------------------------------------------------
# Helper function to fetch a single location-year
def fetch_power_hourly(lat, lon, start, end, params, retries=5):
    url = (
        f"https://power.larc.nasa.gov/api/temporal/hourly/point?"
        f"parameters={','.join(params)}"
        f"&community=AG&longitude={lon}&latitude={lat}"
        f"&start={start}&end={end}&format=CSV"
    )

    for attempt in range(retries):
        r = requests.get(url, timeout=90)
        if r.status_code == 200:
            lines = r.text.splitlines()
            # find the header row that starts with "YEAR"
            header_idx = next(i for i, line in enumerate(lines) if line.startswith("YEAR"))
            csv_data = "\n".join(lines[header_idx:])
            df = pd.read_csv(StringIO(csv_data))
            return df

        elif r.status_code == 429:
            wait = 10 * (attempt + 1)
            print(f"429 Too many requests. Waiting {wait}s before retry...")
            time.sleep(wait)

        else:
            print(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()

    print("Failed after retries.")
    return None

# --------------------------------------------------------
# Main bulk loop
if __name__ == "__main__":
    centroids = pd.read_csv("data/refs/country_centroids.csv")

    params = [
    "ALLSKY_SFC_SW_DWN",    # total shortwave W/m²
    "CLRSKY_SFC_SW_DWN",    # clear-sky shortwave (proxy for clouds)
    "ALLSKY_SFC_SW_DIFF",   # diffuse shortwave
    "T2M",                  # air temperature
    "RH2M",                 # relative humidity
    "WS2M"                  # wind speed
]

    out_dir = Path("data/raw/power_hourly")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Limit to recent 6 years for LED control
    years = range(2018, 2024)       # 2018-2023 inclusive
    
    subset = centroids

    for _, row in subset.iterrows():
        country = row["ADMIN"].replace(" ", "_")
        lat, lon = row["lat"], row["lon"]

        for y in years:
            outfile = out_dir / f"{country}_{y}.csv"
            if outfile.exists():
                print(f"Already have: {outfile.name}")
                continue

            print(f"Fetching {country} {y} ...")
            df = fetch_power_hourly(lat, lon, f"{y}0101", f"{y}1231", params)

            if df is not None and not df.empty:
                df["country"] = country
                df.to_csv(outfile, index=False)
                print(f"Saved {outfile.name} ({len(df)} rows)")
            else:
                print(f"No data for {country} {y}")

            time.sleep(2)    # be gentle on API
