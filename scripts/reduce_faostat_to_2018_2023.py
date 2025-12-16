import pandas as pd
from pathlib import Path

# paths
src = Path("data/raw/faostat/yields_1990_2023.csv")
out = Path("data/processed/foastat_crops_2018_2023.csv")

#read cv
df = pd.read_csv(src)
df_filtered = df[df['year'].between(2018, 2023)]

print(f"Original rows: {len(df)}, Filtered rows: {len(df_filtered)}")

#save
out.parent.mkdir(parents=True, exist_ok=True)
df_filtered.to_csv(out, index=False)