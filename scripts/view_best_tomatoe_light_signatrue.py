import pandas as pd
from pathlib import Path

faostat_file = Path("data/processed/best_yield_with_spectral_hourly.csv")

#load
print(f"Loading dataset: {faostat_file}")
df = pd.read_csv(faostat_file)
print("columns in merged dataset:", list(df.columns)[:15], "...")

#keep only rows for tomatoes
tomatoes = df[df['crop'] == "Tomatoes"].copy()
print(f"\nTotal rows for tomatoes: {len(tomatoes)}")

#country-year with highest yield
best =(
    tomatoes[['country', 'YEAR', 'yield_t_ha']]
    .dropna()
    .sort_values(by='yield_t_ha', ascending=False)
    .iloc[0]
)

best_country = best['country']
best_year = best['YEAR']
best_yield = best['yield_t_ha']
print(f"\nBest tomatoes Yield:")
print(f"country: {best_country}")
print(f"year: {best_year}")
print(f"best yield: {best_yield}")


