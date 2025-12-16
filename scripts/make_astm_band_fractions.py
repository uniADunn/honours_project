import pandas as pd
from pathlib import Path

#load the band integrals
bands_file = Path("data/refs/astm_g173_band_integrals.csv")
df = pd.read_csv(bands_file)

print("Loaded band integrals")
print(df)

#choose PAR-related bands
# bands in use [uv-a, blue, green, red, far-red]
par_related = ["UV_B","UV_A", "Blue", "Green", "Red", "FarRed","NIR", "SW_IR", "IR_Tail"]
sel = df[df['band'].isin(par_related)].copy()

#total of these bands
# total_280_750 = df.loc[df['band'] == "Total_280_750", 'integrated_W_m2'].values[0]
# total_280_2500 = df.loc[df['band'] == "Total_280_2500", 'integrated_W_m2'].values[0]
total_280_4000 = df.loc[df['band'] == "Total_280_4000", 'integrated_W_m2'].values[0]


#add fraction column
# sel['fraction_of_280_750'] = sel['integrated_W_m2'] / total_280_750
# sel['fraction_of_280_2500'] = sel['integrated_W_m2'] / total_280_2500
sel['fraction_of_280_4000'] = sel['integrated_W_m2'] / total_280_4000

print("\nComputed fractions:")
print(sel[['band', 'fraction_of_280_4000']])

#save to csv
out_file = Path("data/refs/astm_band_fractions.csv")
sel.to_csv(out_file, index=False)
print(f"\nSaved fractions to {out_file}")

