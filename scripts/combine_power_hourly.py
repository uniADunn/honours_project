import pandas as pd
from pathlib import Path
import re

#paths
src_dir = Path("data/raw/power_hourly")
out_file = Path("data/processed/power_hourly_2018_2023.csv")
out_file.parent.mkdir(parents=True, exist_ok=True)

all_frames = []

for f in src_dir.glob("*.csv"):
    try:
        #parse country and year from filename
        m = re.search(r'^(.*)_(\d{4})$', f.stem)
        if m:
            country = m.group(1)
            year = int(m.group(2))
        else:
            print("cannot parse filename:", f.name)
            continue

        df = pd.read_csv(f)

        #add columns
        df['country'] = country
        df['year'] = year

        all_frames.append(df)

    except Exception as e:
        print(f"Skipping {f.name} due to error: {e}")

print(f"Found {len(all_frames)} files to merge...")

if all_frames:
    big_df = pd.concat(all_frames, ignore_index=True)
    print("combined shape:", big_df.shape)
    print(big_df.head())

    big_df.to_csv(out_file, index=False)
    print(f"Saved combined CSV to {out_file}")
else:
    print("No hourly files found!")
