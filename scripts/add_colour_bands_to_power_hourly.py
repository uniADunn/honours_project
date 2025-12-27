import pandas as pd
from pathlib import Path

#paths
in_file = Path("data/processed/power_hourly_2018_2023.csv")
fraction_file = Path("data/refs/astm_band_fractions.csv")
out_file = Path("data/processed/power_hourly_2018_2023_spectral.csv")

print(f"Loading hourly dataset: {in_file}")
df = pd.read_csv(in_file)

df.columns = df.columns.str.strip()
print("Columns in hourly dataset:", df.columns.tolist())
if "ALLSKY_SFC_SW_DWN" not in df.columns:
    raise ValueError("ALLSKY_SFC_SW_DWN column not found!")

# show first few radiation values
print("First few ALLSKY_SFC_SW_DWN values:", df["ALLSKY_SFC_SW_DWN"].head().tolist())


print(f"loading band fractions: {fraction_file}")
fractions_df = pd.read_csv(fraction_file)

#prepare fractions
# f_2500 = (
#     fractions_df
#     .set_index("band")["fraction_of_280_2500"]
#     .to_dict()
# )

# f_750 = (
#     fractions_df
#     .set_index("band")["fraction_of_280_750"]
#     .to_dict()
# )
f_4000=(
    fractions_df
    .set_index("band")["fraction_of_280_4000"]
    .to_dict()
)

# print("bands from f_750:", list(f_750.keys()))

# print("Loaded band fractions for both ranges:")
# for b in f_2500.keys():
#     print(f"{b:<8} 280-2500: {f_2500[b]:.4f}")

# for b in f_750.keys():
#     print(f"{b:<8} 280-750: {f_750[b]:.4f}")

# #debud: check which columns got added
# print("New 280-750 columns created:",
#       [c for c in df.columns if c.endswith("_280_750")])


#compute band columns for each range
# for band in f_2500.keys():
#     new_col = f"{band}_W_m2_280_2500"
#     df[new_col] = df["ALLSKY_SFC_SW_DWN"] * f_2500[band]

# for band in f_750.keys():
#     new_col = f"{band}_W_m2_280_750"
#     df[new_col] = df["ALLSKY_SFC_SW_DWN"] * f_750[band]

for band in f_4000.keys():
    new_col = f"{band}_W_m2_280_4000"
    df[new_col] = df["ALLSKY_SFC_SW_DWN"] * f_4000[band]


#check which columns were added
# added_cols_2500 = [c for c in df.columns if c.endswith("_W_m2_280_2500")]
# added_cols_750 = [c for c in df.columns if c.endswith("_W_m2_280_750")]
added_cols_4000 = [c for c in df.columns if c.endswith("_W_m2_280_4000")]

# print(f"\nNew 280-2500 columns added: {added_cols_2500}")
# print(f"New 280-750 columns added:  {added_cols_750}")
print(f"New 280-4000 columns added:  {added_cols_4000}")

# preview
# preview_cols = ["YEAR", "MO", "DY", "HR", "ALLSKY_SFC_SW_DWN"] + added_cols_2500 + added_cols_750
preview_cols = ["YEAR", "MO", "DY", "HR", "ALLSKY_SFC_SW_DWN"] + added_cols_4000
print("\nPreview of added columns:")
print(df[preview_cols].head(10))

#save
df.to_csv(out_file, index=False)
print(f"\nSaved new spectral hourly dataset to: {out_file}")

    