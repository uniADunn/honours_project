import pandas as pd
from pathlib import Path

#paths
faostat_path = Path("data/processed/faostat_crops_2018_2023.csv")
df = pd.read_csv(faostat_path)

print(df.head(20))
print(df.dtypes)
print(df['yield_t_ha'].unique()[:30])
