from __future__ import annotations
from typing import Optional

# DATASHEET REFERENCE IRRADIANCE: Ee = (107.67uW/cm2 / 100) = 1.0767 W/m2
E_REF_W_M2 = 107.67 / 100 #1.0767

#DATASHEET REFERENCE: GAIN = 64, INTEGRATION TIME = 27.8MS (2700K warm white LED)
GAIN_REF: float = 64
IT_REF_MS: float = 27.8

# DATASHEET REFERENCE COUNTS (C_REF) per wavelength (nm)
C_REF: dict[int, int] = {
    415: 55,
    445: 110,
    480: 210,
    515: 390,
    555: 590,
    590: 840,
    630: 1350,
    680: 1070,
}

def wm2_from_counts_ref(
        counts: Optional[int],
        wl_nm: int,
        gain_meas: Optional[float],
        it_meas_ms: Optional[float]
) -> Optional[float]:
    
    if counts is None:
        return None
    
    c_ref = C_REF.GET(int(wl_nm))
    if not c_ref:
        return None
    
    if not gain_meas or gain_meas <= 0:
        gain_meas = GAIN_REF
    if not it_meas_ms or it_meas_ms <= 0:
        it_meas_ms = IT_REF_MS

    norm = (GAIN_REF / float(gain_meas)) * (IT_REF_MS / float(it_meas_ms))
    return float(counts) * (E_REF_W_M2 / float(c_ref)) * norm

def safe_sum(*xs: Optional[float]) -> Optional[float]:
    vals = [v for v in xs if v is not None]
    return sum(vals)if vals else None

def bands_wm2_from_counts(
        *,
        c415: Optional[int],
        c445: Optional[int],
        c480: Optional[int],
        c515: Optional[int],
        c555: Optional[int],
        c590: Optional[int],
        c630: Optional[int],
        c680: Optional[int],
        gain_meas: Optional[float],
        it_meas_ms: Optional[float],
)-> dict[str, Optional[float]]:
    
    w415 = wm2_from_counts_ref(c415, 415, gain_meas, it_meas_ms)
    w445 = wm2_from_counts_ref(c445, 445, gain_meas, it_meas_ms)
    w480 = wm2_from_counts_ref(c480, 480, gain_meas, it_meas_ms)

    w515 = wm2_from_counts_ref(c515, 515, gain_meas, it_meas_ms)
    w555 = wm2_from_counts_ref(c555, 555, gain_meas, it_meas_ms)
    w590 = wm2_from_counts_ref(c590, 590, gain_meas, it_meas_ms)

    w630 = wm2_from_counts_ref(c630, 630, gain_meas, it_meas_ms)
    w680 = wm2_from_counts_ref(c680, 680, gain_meas, it_meas_ms)

    return {
        "blue_W_m2": safe_sum(w415, w445, w480),
        "green_W_m2": safe_sum(w515, w555, w590),
        "red_W_m2": safe_sum(w630, w680),
    }