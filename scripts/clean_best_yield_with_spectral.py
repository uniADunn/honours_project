import pandas as pd
from pathlib import Path

#paths
infile = Path("data/processed/best_yield_with_spectral_hourly.csv")
outfile = infile

df=pd.read_csv(infile)

if 'year_y' in df.columns:
    df = df.drop(columns='year_y')

if 'year_x' in df.columns:
    df = df.drop(columns='year_x')

print(f"cleanded columns {list(df.columns)}")

#save
df.to_csv(outfile, index=False)
print(f"Saved cleaned data set back to: {outfile}")
