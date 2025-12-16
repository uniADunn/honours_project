import numpy as np
import pandas as pd
from pathlib import Path

#paths
src = Path("data/refs/astm_g173_clean.csv")
out = Path("data/refs/astm_g173_band_integrals.csv")

df = pd.read_csv(src)

#use global tilt spectrum at the ground
wl = df["wavelength_nm"]
E = df["GlobalTilt_W_m2_nm"]

# AM1.5 file has 0.5-nm spacing, so each value represents 0.5nm bin
d_lambda = wl.diff().median()
print(f"(delta lambda) Wavelength step: ", d_lambda, "nm")


# colour bands
bands = {
    "UV_B": (280, 315),
    "UV_A": (315, 400),
    "Blue": (400, 500),
    "Green": (500, 600),
    "Red": (600, 700),
    "FarRed": (700, 750),
    "NIR": (750, 1100),
    "SW_IR":(1100, 2500),
    "IR_Tail":(2500,4000),
    "Total_280_4000": (280,4000)
}
results = []

for band, (lo, hi) in bands.items():
    mask = (wl>= lo) & (wl<hi)
    integral_W_m2 = np.trapezoid(y=(E[mask].to_numpy()), x=(wl[mask].to_numpy()))  # integrate using trapezoidal rule
    results.append({"band": band,
                    "wavelength_min_nm": lo,
                    "wavelength_max_nm": hi,
                    "integrated_W_m2": integral_W_m2})
    
res_df = pd.DataFrame(results)
print(res_df["band"].value_counts())

#compute % of total (280-4000nm)
total = res_df.loc[res_df["band"] == "Total_280_4000","integrated_W_m2"].iloc[0]
res_df["percent_of_total"] = 100 * res_df["integrated_W_m2"] / total
print(res_df)

out.parent.mkdir(parents=True, exist_ok=True)
res_df.to_csv(out, index=False)
print(f"\nSaved band integrals to {out}")

