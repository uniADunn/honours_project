import pandas as pd
import zipfile

faostat_zip = "data/raw/faostat/FAOSTAT_AllData.zip"
output_csv  = "data/raw/faostat/yields_1990_2023.csv"

# --- open the correct csv inside the zip ---
with zipfile.ZipFile(faostat_zip) as z:
    csv_name = [f for f in z.namelist() if "All_Data.csv" in f][0]
    with z.open(csv_name) as f:
        df = pd.read_csv(f, low_memory=False)

print("Raw shape:", df.shape)
print("Columns sample:", df.columns[:12])
print(df.dtypes)

# ---------- reshape to long ----------
year_cols = [c for c in df.columns if c.startswith("Y")]
id_cols   = [c for c in df.columns if not c.startswith("Y")]


df_long = df.melt(id_vars=id_cols,
                  value_vars=year_cols,
                  var_name="year_raw",
                  value_name="value")

# convert Y1990 → 1990
df_long["year"] = df_long["year_raw"].str.extract(r"Y(\d{4})").astype(int)

# filter years 1990–2023
df_long = df_long[(df_long["year"] >= 1990) & (df_long["year"] <= 2023)]

# keep only rows where Element == Yield
df_long = df_long[df_long["Element"] == "Yield"]

# crops we care about
crops = [
    "Tomatoes",
    "Cucumbers and gherkins",
    "Chillies and peppers, green",
    "Strawberries",
    "Lettuce and chicory",
    "Cabbages and other brassicas",
    "Onions, dry",
    "Spinach",
    "Eggplants (aubergines)"
]
df_long = df_long[df_long["Item"].isin(crops)]

# select + rename columns
df_out = df_long.loc[:, [
    "Area", "Area Code (M49)", "Item", "year", "Unit", "value"
]].copy()

df_out.rename(columns={
    "Area": "country",
    "Area Code (M49)": "m49_code",
    "Item": "crop",
    "value": "yield_t_ha"
}, inplace=True)

# save tidy file
df_out.to_csv(output_csv, index=False)
print(f"Saved {output_csv}  rows:{len(df_out)}")
print(df_out.head())
