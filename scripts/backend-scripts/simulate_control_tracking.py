

BANDS = ("blue", "green", "red")

#placeholders
DT_S = 5.0
HOUR_S = 3600.0

# placeholder max outputs from calibration (J/m2/hour)
CAPS_J_PER_HOUR = {
    "blue": 728357.7226,
    "green": 422294.8749,
    "red": 129824.7369,
}

TARGETS_J_PER_HOUR = {
    "blue": 8000,
    "green": 7000,
    "red": 6000,
}

def pct(val):
    return 100.0 * val

def caps_to_power_per_s(caps):
    #convert J per hour to J per second (W/m2)
    return {b: caps[b] / HOUR_S for b in BANDS}

def compute_duty_factor(accumulated, targets, power_per_sec, remaining_time_s):
    # compute duty factor u (0 -> 1) for a single full-spectrum lamp
    """
    accumulated: current accumulated J per band
    targets: target J per band - for the hour
    power_per_sec: max lamp output per band (J/s)
    remaining_time_s: remaining time in the hour (s)
    """
    ratios = []

    for band in BANDS:
        remaining_dose = targets[band] - accumulated[band]

        # if already at or above target -> dont add more
        if remaining_dose <= 0:
            ratios.append(0.0)
            continue

        max_possible = power_per_sec[band] * remaining_time_s

        # if lamp cant deliver anything -> 0
        if max_possible <= 0:
            ratios.append(0.0)
            continue
        
        ratios.append(remaining_dose / max_possible)

    if not ratios:
        return 0.0

    #choose the smallest ratio to ensure we dont overshoot any band
    u = min(1.0, min(ratios))

    # keep within 0-1
    if u < 0:
        u = 0.0

    return u

def simulate_single_lamp():
    power_per_sec = caps_to_power_per_s(CAPS_J_PER_HOUR)

    accumulated = {b: 0.0 for b in BANDS}

    t = 0.0
    duties = []

    while t < HOUR_S:
        remaining_time = HOUR_S - t
        # compute duty factor
        u = compute_duty_factor(accumulated, TARGETS_J_PER_HOUR, power_per_sec, remaining_time)
        duties.append(u)

        # apply energy for this timestep
        for band in BANDS:
            accumulated[band] += u * power_per_sec[band] * DT_S

        t += DT_S
    
    print("\nTracking error:")
    for band in BANDS:
        error = TARGETS_J_PER_HOUR[band] - accumulated[band]
        print(f"{band}: error = {error:.2f} J/m2")

    print("\nDuty Factor summary:")
    print(f"Min u: {min(duties)}")
    print(f"Max u: {max(duties)}")
    print(f"final u: {duties[-1]}")
    print(f"avg u: {sum(duties)/len(duties)}")

    avg_u = sum(duties) / len(duties)

    print("\nSingle lamp: effective power used")
    print(f"avg u: = {avg_u:.6f} ({pct(avg_u):.3f}%)")
    print(f"min u: = {min(duties):.6f} ({pct(min(duties)):.3f}%)")
    print(f"max u: = {max(duties):.6f} ({pct(max(duties)):.3f}%)")


    print("\nDuty snapshot (every 10 minutes):")
    steps_per_10min = int(10*60 / DT_S)
    for i in range(0, len(duties), steps_per_10min):
        minute = int((i * DT_S) / 60)
        print(f"t={minute:02d} min: u={duties[i]:.3f}")

    print("\nFINAL ACCUMULATED AFTER 1 HOUR:")
    for band in BANDS:
        print(f"{band}: {accumulated[band]:.2f} J/m2")

def simulate_independent_leds():
    duties_by_band = {b: [] for b in BANDS}
    power_per_sec = caps_to_power_per_s(CAPS_J_PER_HOUR)

    accumulated = {b: 0.0 for b in BANDS}

    t = 0.0

    while t < HOUR_S:
        remaining_time = HOUR_S - t
        
        for band in BANDS:
            remaining_dose = TARGETS_J_PER_HOUR[band] - accumulated[band]

            if remaining_dose <= 0:
                continue

            max_possible = power_per_sec[band] * remaining_time

            if max_possible <= 0:
                continue

            u_band = min(1.0, remaining_dose / max_possible)
            duties_by_band[band].append(u_band)

            accumulated[band] += u_band * power_per_sec[band] * DT_S
        
        t += DT_S

    print("\nIndependent LED MODEL final:")
    for band in BANDS:
        print(f"{band}: {accumulated[band]:.2f} J/m2")

    print("\nIndependent LEDs: per-band power used (avg duty)")
    for band in BANDS:
        if duties_by_band[band]:
            avg_u = sum(duties_by_band[band]) / len(duties_by_band[band])
        else:
            avg_u = 0.0

        print(f"{band}: avg u = {avg_u:.6f} ({pct(avg_u):.3f}%)")

if __name__ == "__main__":
    print("SIMULATING SINGLE FULL-SPECTRUM LAMP CONTROL TRACKING:")
    simulate_single_lamp()

    print("\nSIMULATING INDEPENDENT LED CONTROL TRACKING:")
    simulate_independent_leds()