from pathlib import Path
import pandas as pd

src = Path("data/refs/ASTMG173.csv")
out = Path("data/refs/astm_g173_clean.csv")

#the real header is on the 2nd row (skip the first line)
df = pd.read_csv(src, skiprows=1)

print("Original columns: ", list(df.columns))

#rename for clarity
df = df.rename(columns={
    df.columns[0]: "wavelength_nm",
    df.columns[1]: "ETR_W_m2_nm", #extraterristrial spectrum
    df.columns[2]: "GlobalTilt_W_m2_nm", # global tilted spectrum
    df.columns[3]: "DirectCirc_W_m2_nm" #direct + circumsolar
})

#drop rows with NaN wavelength
df = df[pd.to_numeric(df["wavelength_nm"], errors="coerce").notna()]

#convert all numeric
for c in df.columns:
    df[c] = pd.to_numeric(df[c], errors="coerce")

print(df.head(10))
print(f"Rows after cleaning: {len(df)}")

#save cleaned version
out.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(out, index=False)
print(f"Saved cleaned file to {out}")
