import pandas as pd
import matplotlib.pyplot as plt

# --- Load dataset ---
df = pd.read_csv("data/processed/best_yield_with_spectral_hourly.csv")

# --- Filter for Belgium 2020 Tomatoes ---
belgium2020 = df[(df['country'] == 'Belgium') &
                 (df['crop'] == 'Tomatoes') &
                 (df['YEAR'] == 2020)].copy()

# Group by month to get average irradiance for each band
monthly = (
    belgium2020
    .groupby('MO')[['Blue_W_m2_280_750',
                    'Green_W_m2_280_750',
                    'Red_W_m2_280_750',
                    'FarRed_W_m2_280_750']]
    .mean()
    .reset_index()
    .rename(columns={'MO': 'month'})
)

# Colors for each band
colors = {
    'Blue_W_m2_280_750': 'blue',
    'Green_W_m2_280_750': 'green',
    'Red_W_m2_280_750': 'red',
    'FarRed_W_m2_280_750': 'black'
}

# Unique markers for each band
markers = {
    'Blue_W_m2_280_750': 's',   # square
    'Green_W_m2_280_750': 'o',  # circle
    'Red_W_m2_280_750': '^',    # triangle
    'FarRed_W_m2_280_750': 'D'  # diamond
}

# --- Plot ---
plt.figure(figsize=(10, 6))

bands = list(colors.keys())

for band in bands:
    plt.plot(
        monthly['month'],
        monthly[band],
        label=band.split('_')[0],          # Shorter legend names
        color=colors[band],
        marker=markers[band],              # Unique marker
        markersize=7,
        linewidth=2                         # Thicker lines
    )

plt.title("Belgium 2020 – Tomatoes\nAverage Monthly Spectral Irradiance")
plt.xlabel("Month (Jan–Dec)")
plt.ylabel("Average Irradiance (W/m²)")
plt.xticks(range(1, 13), 
           ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])
plt.legend()
plt.grid(True)
plt.tight_layout()

plt.show()
