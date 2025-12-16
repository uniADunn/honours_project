import pandas as pd
from pathlib import Path

#path to the astm g173 csv file.
p = Path("data/refs/ASTMG173.csv")

#robust read (auto-detect comma/semicolon/tab)
#header is on 2nd line skip first line
df = pd.read_csv(p, skiprows=1)

print("Columns found:")
print(list(df.columns))
print()

print("First 10 rows:")
print(df.head(10).to_string(index=False))
print()

wl = pd.to_numeric(df.iloc[:,0], errors="coerce")
print(f"Rows: {len(df)}")
print(f"Wavelength min/max (nm): {wl.min()} .. {wl.max()}")
print(f"Non-numeric wavelength rows (if any): {(wl.isna()).sum()}")