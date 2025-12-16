import pandas as pd
import requests
from io import StringIO
from pathlib import Path
import time

#helper to fetch one location-year
def fetch_power_daily(lat,lon,start,end, params):
    url=(
        f"https://power.larc.nasa.gov/api/temporal/daily/point?"
        f"parameters={','.join(params)}"
        f"&community=AG&longitude={lon}&latitude={lat}"
        f"&start={start}&end={end}&format=CSV"
    )
    r = requests.get(url)
    r.raise_for_status()

    #cut of header lines
    lines = r.text.splitlines()
    header_index = next(i for i, line in enumerate(lines) if line.startswith("YEAR"))
    csv_data="\n".join(lines[header_index:])
    df=pd.read_csv(StringIO(csv_data))
    return df

# main bulk loop -
if __name__ == "__main__":
    centroids = pd.read_csv("data/refs/country_centroids.csv")

    params = [
        "ALLSKY_SFC_PAR_TOT",
        "ALLSKY_SFC_SW_DWN",
        "T2M", "T2M_MAX", "T2M_MIN",
        "PRECTOTCORR"
    ]
    out_dir = Path("data/raw/power")
    out_dir.mkdir(parents=True, exist_ok=True)

    #choose a small test subset first (to avoid hammering the api)
    years = range(1990, 1993)
    subset = centroids.head(3)

    for _, row in subset.iterrows():
        country = row["ADMIN"].replace(" ", "_")
        lat, lon = row["lat"], row["lon"]

        for y in years:
            outfile = out_dir / f"{country}_{y}.csv"
            if outfile.exists():
                print("Already have:", outfile)
                continue

            print(f"Fetching {country} {y} ...")
            df = fetch_power_daily(lat, lon, f"{y}0101", f"{y}1231", params)
            df.to_csv(outfile, index=False)
            print( " saved", outfile)
            time.sleep(2) # being gentle on the API