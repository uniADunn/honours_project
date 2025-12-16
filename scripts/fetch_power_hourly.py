import pandas as pd
import requests
from pathlib import Path
import time
from io import StringIO

# settings
centroids = pd.read_csv("data/refs/country_centroids.csv")




#Hourly data available ~2018 - 2023
start_year = 2018
end_year = 2023

BASE_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"

def fetch_hourly(lat, lon, start, end, parameters, retries=5):
    """Fetch hourly data from NASA POWER and return as dataframe"""
    url = (
        f"{BASE_URL}"
        f"?parameters={parameters}"
        f"&community=AG"
        f"&longitude={lon:.2f}"
        f"&latitude={lat:.2f}"
        f"&start={start}"
        f"&end={end}"
        f"&format=CSV"
    )
    for attempt in range(retries):
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            lines = r.text.splitlines()
            header_index = next(i for i, line in enumerate(lines) if line.startswith("YEAR"))
            csv_data="\n".join(lines[header_index:])
            df=pd.read_csv(StringIO(csv_data))
            return df
        elif r.status_code == 429:
            wait = 5*(attempt + 1)
            print(f"429 Too many requests. Waiting {wait}'s before retry...")
            time.sleep(wait)
        else:
            r.raise_for_status()
            print("Failed after retries")
            return None
    
# main bulk loop
if __name__ == "__main__":
    centroids = pd.read_csv("data/refs/country_centroids.csv")

    params = [
    "ALLSKY_SFC_PAR_TOT",
    "T2M", "T2M_MAX", "T2M_MIN"
    ]

    out_dir = Path("data/raw/power_hourly")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir /"power_hourly_2018_2023.csv"

    #hourly data only available 2018-2023
    years = range(2023-2024)
    subset = centroids.head(2)

    all_frames = []

    for _, row in subset.iterrows():
        country = row["ADMIN"].replace(" ", "_")
        lat,lon = row["lat"], row["lon"]

        for y in years:
            for m in range(1, 13):
                start = f"{y}{m:02d}01"
                end = f"{y}{m:02d}31"

                outfile = out_dir/f"{country}_{y}_{m:02d}.csv"
                if outfile.exists():
                    print("Already have:", outfile)
                    continue

                print(f"Fetching {country} {y}-{m:02d} ...")
                df = fetch_hourly(lat,lon, start, end, params)
                if df is not None and not df.empty:
                    df["country"] = country
                    all_frames.appened(df)                    
                    print(f" Collected {len(df)} rows")
                else:
                    print(f"no data for {country} {y}-{m:02d}")

                time.sleep(4) #gentle on API

if all_frames:
    big_df = pd.concat(all_frames, ignore_index=True)
    big_df.to_csv(out_file, index=False)
    print(f"\n saved combined hourly data to: {out_file}")
    print("Total rows:", len(big_df))
    print(big_df.head())
else:
    print("No data collected!")