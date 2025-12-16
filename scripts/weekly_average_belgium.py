import pandas as pd
import matplotlib.pyplot as plt

# === Load dataset ===
df = pd.read_csv("data/processed/best_yield_with_spectral_hourly.csv")

# === Filter for Belgium 2020 Tomatoes ===
belgium2020 = df[
    (df['country'] == 'Belgium') &
    (df['crop'] == 'Tomatoes') &
    (df['YEAR'] == 2020)
].copy()

# --- Rename columns for datetime assembly ---
belgium2020 = belgium2020.rename(
    columns={'YEAR': 'year', 'MO': 'month', 'DY': 'day', 'HR': 'hour'}
)

# --- Combine into datetime ---
belgium2020['datetime'] = pd.to_datetime(
    belgium2020[['year', 'month', 'day', 'hour']]
)

# --- Derive ISO week number ---
belgium2020['week'] = belgium2020['datetime'].dt.isocalendar().week
belgium2020['week'] = belgium2020['week'].astype(int)

# --- Aggregate weekly averages ---
weekly = (
    belgium2020
    .groupby('week', as_index=False)
    [['Blue_W_m2_280_750',
      'Green_W_m2_280_750',
      'Red_W_m2_280_750',
      'FarRed_W_m2_280_750']]
    .mean()
)

# --- Plot ---
plt.figure(figsize=(12,6))

colors = {
    'Blue_W_m2_280_750': 'blue',
    'Green_W_m2_280_750': 'green',
    'Red_W_m2_280_750': 'red',
    'FarRed_W_m2_280_750': 'black'
}

for band, color in colors.items():
    plt.plot(
        weekly['week'],
        weekly[band],
        label=band.split('_')[0],
        color=color,
        marker='o',
        linewidth=1
    )

plt.title("Belgium 2020 - Tomatoes\nAverage Weekly Spectral Irradiance")
plt.xlabel("Week of Year (1-52)")
plt.ylabel("Average Irradiance (W/m²)")
plt.grid(True, alpha=0.3)
plt.legend()
plt.tight_layout()

plt.show()
