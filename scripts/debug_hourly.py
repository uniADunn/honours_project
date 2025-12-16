import pandas as pd
from pathlib import Path

hourly_file = Path("data/processed/power_hourly_2018_2023.csv")
fractions_file = Path("data/refs/astm_band_fractions.csv")

print("\n--- Loading hourly data ---")
df = pd.read_csv(hourly_file)
df.columns = df.columns.str.strip()      # remove stray spaces
print("Columns:", df.columns.tolist()[:15])
print("First 5 ALLSKY_SFC_SW_DWN:", df["ALLSKY_SFC_SW_DWN"].head().tolist())

print("\n--- Loading fractions ---")
fractions_df = pd.read_csv(fractions_file)
print(fractions_df)

f_2500 = fractions_df.set_index("band")["fraction_of_280_2500"].to_dict()
print("\n280-2500 fractions:", f_2500)

# try to create one test column
band = "Blue"
df[f"{band}_test"] = df["ALLSKY_SFC_SW_DWN"] * f_2500[band]
print("\nNew column created:", f"{band}_test")
print(df[[ "YEAR", "MO", "DY", "HR", "ALLSKY_SFC_SW_DWN", f"{band}_test"]].head())