import pandas as pd
from pathlib import Path

# --------- CONFIG ---------
SRC = Path("data/processed/belgium_tomatoes_2020_hourly_spectral.csv")
OUT_HOURLY = Path("data/processed/belgium_tomatoes_2020_hourly_with_ppfd.csv")
OUT_DAILY = Path("data/processed/belgium_tomatoes_2020_daily_DLI.csv")

# Column names in your CSV for each band (kWh/m² per hour)
BAND_COLS = {
    "uv_b":   "UV_B_W_m2_280_4000",
    "uv_a":   "UV_A_W_m2_280_4000",
    "blue":   "Blue_W_m2_280_4000",
    "green":  "Green_W_m2_280_4000",
    "red":    "Red_W_m2_280_4000",
    "farred": "FarRed_W_m2_280_4000",
    # add NIR / SW_IR / IR_Tail if you ever want to photon-count those too
}

# --------- CONSTANTS ---------
# mol per kWh for each band, using midpoint wavelength of the band
# (computed from hc/λ and Avogadro's number)
MOL_PER_KWH = {
    "uv_b":   8.952860882378715,   # λ ≈ 297.5 nm
    "uv_a":   10.758479883866858,  # λ ≈ 357.5 nm
    "blue":   13.542142511161083,  # λ ≈ 450 nm
    "green":  16.551507513641322,  # λ ≈ 550 nm
    "red":    19.56087251612156,   # λ ≈ 650 nm
    "farred": 21.817896267981737,  # λ ≈ 725 nm
}

UMOL_PER_M2_S_PER_MOL_PER_H = 1e6 / 3600.0  # 1 mol/h = 277.777... μmol/m²/s


def main():
    # --------- 1. Read CSV ---------
    df = pd.read_csv(SRC)

    # Build datetime and date columns if not present
    if {"YEAR", "MO", "DY", "HR"}.issubset(df.columns):
        df["datetime"] = pd.to_datetime(
            {
                "year": df["YEAR"],
                "month": df["MO"],
                "day": df["DY"],
                "hour": df["HR"],
            }
        )
        df["date"] = df["datetime"].dt.date
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["date"] = df["datetime"].dt.date
    else:
        raise ValueError("Need YEAR/MO/DY/HR or a datetime column in the CSV.")

    # --------- 2. Hourly: kWh -> mol/hour -> PPFD ---------
    for band, col in BAND_COLS.items():
        if col not in df.columns:
            raise KeyError(f"Expected column '{col}' for band '{band}' not found in CSV.")

        mol_col = f"{band}_mol_per_h"
        ppfd_col = f"PPFD_{band}_umol_m2_s"

        # kWh/m² * (mol/kWh) = mol/m² per hour
        df[mol_col] = df[col] * MOL_PER_KWH[band]

        # mol/h -> μmol/m²/s
        df[ppfd_col] = df[mol_col] * UMOL_PER_M2_S_PER_MOL_PER_H

    # --------- 3. Hourly PAR & ePAR ---------
    # PAR = blue + green + red
    df["PAR_mol_per_h"] = (
        df["blue_mol_per_h"] +
        df["green_mol_per_h"] +
        df["red_mol_per_h"]
    )
    df["PPFD_PAR_umol_m2_s"] = df["PAR_mol_per_h"] * UMOL_PER_M2_S_PER_MOL_PER_H

    # ePAR = PAR + far-red (700–750 nm)
    df["ePAR_mol_per_h"] = df["PAR_mol_per_h"] + df["farred_mol_per_h"]
    df["PPFD_ePAR_umol_m2_s"] = df["ePAR_mol_per_h"] * UMOL_PER_M2_S_PER_MOL_PER_H

    # --------- 4. Daily DLI per band (sum mol over each day) ---------
    group_cols = ["date"]
    if "YEAR" in df.columns and "MO" in df.columns and "DY" in df.columns:
        # Helpful to keep these visible in the daily output
        group_cols = ["YEAR", "MO", "DY"]

    daily = (
        df.groupby(group_cols, as_index=False)[
            [
                "uv_b_mol_per_h",
                "uv_a_mol_per_h",
                "blue_mol_per_h",
                "green_mol_per_h",
                "red_mol_per_h",
                "farred_mol_per_h",
                "PAR_mol_per_h",
                "ePAR_mol_per_h",
            ]
        ]
        .sum()
        .rename(
            columns={
                "uv_b_mol_per_h": "DLI_uv_b_mol_m2_d",
                "uv_a_mol_per_h": "DLI_uv_a_mol_m2_d",
                "blue_mol_per_h": "DLI_blue_mol_m2_d",
                "green_mol_per_h": "DLI_green_mol_m2_d",
                "red_mol_per_h": "DLI_red_mol_m2_d",
                "farred_mol_per_h": "DLI_farred_mol_m2_d",
                "PAR_mol_per_h": "DLI_PAR_mol_m2_d",
                "ePAR_mol_per_h": "DLI_ePAR_mol_m2_d",
            }
        )
    )

    # --------- 5. Save outputs ---------
    OUT_HOURLY.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_HOURLY, index=False)
    print(f"Saved hourly PPFD + mol data to: {OUT_HOURLY}")

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(OUT_DAILY, index=False)
    print(f"Saved daily DLI per band to: {OUT_DAILY}")


if __name__ == "__main__":
    main()
