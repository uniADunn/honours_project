import pandas as pd
from pathlib import Path

# Paths
in_file = Path("data/processed/best_yield_with_spectral_hourly.csv")
out_file = Path("data/processed/belgium_tomatoes_2020_hourly_spectral.csv")

print(f"Loading merged dataset from: {in_file}")
df = pd.read_csv(in_file)

print("Columns in file:", df.columns.tolist())

# --- Figure out which year column to use ---
year_col = None
for cand in ["YEAR", "year", "year_x", "year_y"]:
    if cand in df.columns:
        year_col = cand
        break

if year_col is None:
    raise ValueError("No YEAR/year/year_x/year_y column found in the dataset!")

print(f"Using year column: {year_col}")

# --- Filter for Belgium, Tomatoes, 2020 ---
mask = (
    (df["country"] == "Belgium") &
    (df["crop"] == "Tomatoes") &
    (df[year_col] == 2020)
)

belgium_tom_2020 = df[mask].copy()

print(f"Rows for Belgium, Tomatoes, {2020}: {len(belgium_tom_2020)}")

if len(belgium_tom_2020) == 0:
    print("No rows found – check country name, crop name, or year.")
else:
    # Save to CSV
    out_file.parent.mkdir(parents=True, exist_ok=True)
    belgium_tom_2020.to_csv(out_file, index=False)
    print(f"Saved filtered data to: {out_file}")
