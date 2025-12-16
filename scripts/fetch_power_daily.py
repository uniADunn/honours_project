import requests
import pandas as pd
from io import StringIO

def fetch_power_daily(lat, lon, start, end, params):
    """Fetch NASA POWER daily data for one lat/lon & date range."""
    url=(
        f"https://power.larc.nasa.gov/api/temporal/daily/point?"
        f"parameters={','.join(params)}"
        f"&community=AG&longitude={lon}&latitude={lat}"
        f"&start={start}&end={end}&format=CSV"
    )

    print("Fetching:", url)
    r=requests.get(url)
    r.raise_for_status()

    print("HTTP status:", r.status_code)
    print("- First 20 lines fo response -")
    for line in r.text.splitlines()[:20]:
        print(line)
    print("- End of preview -")
    
    #POWER CSV has comment lines starting with '#'
    lines = r.text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("YEAR"):
            header_index=i
            break

    csv_data = "\n".join(lines[header_index:])
    df = pd.read_csv(StringIO(csv_data))
    return df

if __name__ == "__main__":
    #pick a test country & year
    # example: zimbabwe centroid
    lat,lon = -19.0, 29.85
    params = [
        "ALLSKY_SFC_PAR_TOT",
        "ALLSKY_SFC_SW_DWN",
        "T2M", "T2M_MAX", "T2M_MIN",
        "PRECTOTCORR"
    ]
    
    df= fetch_power_daily(lat,lon, 19900101, 19901231, params)
    print(df.head())
    print(df.columns)

    #save test file
    df.to_csv("data/raw/power/ZIMBABWE_1990.csv", index=False)
    print("Saved daily test file:", "data/raw/power/ZIMBABWE_1990.csv")
