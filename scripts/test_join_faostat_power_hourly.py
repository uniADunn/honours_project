import pandas as pd
from pathlib import Path

faostat_path = Path("data/processed/foastat_crops_2018_2023.csv")
power_path = Path("data/processed/power_hourly_2018_2023_spectral.csv")
#load data
faostat = pd.read_csv(faostat_path)
power = pd.read_csv(power_path)

print(faostat.info())
print()
print(power.info())

#merge
merged = pd.merge(
    faostat,
    power,
    on=["country", "year"],
    how="inner"
)

print(merged.head())