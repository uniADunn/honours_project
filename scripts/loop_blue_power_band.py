# read in csv file
import pandas as pd

df = pd.read_csv('data/processed/belgium_tomatoes_2020_hourly_spectral.csv')
# calculate total blue power in 280-4000nm range for that day
#get just the first day (24 hours)
mask = (df['YEAR'] == 2020) & (df['MO'] == 1) & (df['DY'] == 1)
day1 = df[mask]

blue_band_sum = day1['Blue_W_m2_280_4000'].sum()
print(f"Total blue power (280-4000nm) for Belgium tomatoes on January 1, 2020: {blue_band_sum} kWh/m2")



####################################################################################################################

total_blue_power = df['Blue_W_m2_280_4000'].sum()
print(f"Total blue power (280-4000nm) for Belgium tomatoes 2020 hourly spectral data: {total_blue_power} kWh/m2")

E_blue_j_year = total_blue_power * 3.6e6
print(f"Total blue energy for the year in joules: {E_blue_j_year} J/m2")

blue_band_peak_wavelength = 450e-9
print(f"Blue band peak wavelength: {blue_band_peak_wavelength} m")

# constants 
H = 6.62607015e-34 # Planck's constant (Joule seconds)
C = 3e8 # Speed of light (m/s)
E_photon_blue_band = H * C / blue_band_peak_wavelength
print(f"Energy per photon in blue band: {E_photon_blue_band} J")

N_blue_photons_year = E_blue_j_year / E_photon_blue_band
print(f"Total number of blue photons for the year: {N_blue_photons_year:.2e} photons/m2")

NA = 6.022e23 # Avogadro's number (photons per mole)
blue_mol_year = N_blue_photons_year / NA
print(f"Total moles of blue photons for the year: {blue_mol_year:.6f} mol/m2")

