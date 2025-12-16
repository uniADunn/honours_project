import pandas as pd
from pathlib import Path

#paths
faostat_path = Path("data/processed/faostat_crops_2018_2023.csv")
power_hourly_path = Path("data/processed/power_hourly_2018_2023_spectral.csv")
outfile = Path("data/Processed/best_yield_with_spectral_hourly.csv")

#load faostat crops data
print(f"Loading FAOSTAT data: {faostat_path}")
faostat = pd.read_csv(faostat_path)

#convert collumn to numeric
faostat['yield_t_ha'] = pd.to_numeric(faostat['yield_t_ha'], errors='coerce')

before_drop = len(faostat)
faostat = faostat.dropna(subset=['yield_t_ha'])
print(f"Dropped {before_drop - len(faostat)} rows with no reported yield")

print("columns in FAOSTAT:", list(faostat.columns))
print(faostat['year'].unique())


#get best yield for each crop and country
faostat_best = (
    faostat
    .sort_values(['country', 'crop', 'yield_t_ha'], ascending=[True, True, False])
    .groupby(['country', 'crop'], as_index=False)
    .first()
)

print(f"best yield rows per crop and country: {faostat_best.shape[0]} rows")
print(faostat_best.head())


print("joining best-yield faostat with hourly spectral data...")
# load hourly spectral power data
print(f"Loading Power hourly spectral: {power_hourly_path}")
power_hourly = pd.read_csv(power_hourly_path)

#columns in power hourly spectral
print("columns in power hourly spectral:", list(power_hourly.columns))
 
merged = pd.merge(
    power_hourly,
    faostat_best,
    left_on=['country', 'YEAR'],
    right_on=['country', 'year'],
    how='inner'
)
print(f"Merged dataset shape: {merged.shape}")

#save
outfile.parent.mkdir(parents=True, exist_ok=True)
merged.to_csv(outfile, index=False)
print(f"Saved merged file to {outfile}")
