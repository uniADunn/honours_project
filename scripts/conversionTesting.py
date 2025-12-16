#Values from belgium tomatoes 2020 hourly spectral data (1/1/2020 9-10am)
#Fraction of blue and green light in 280-4000nm range
blue_W_m2_280_4000 = 0.060731; #blue fraction of 280-4000nm (W/m2)
green_W_m2_280_4000 = 0.065778; #green fraction of 280-4000nm (W/m2)
red_W_m2_280_4000 = 0.060678; #red fraction of 280-4000nm (W/m2)

#Step 1: convert kWh/m2 to joules
#kWh/m2 --> mol m-2 (joules)
print("Conversion Step 1: kWh/m2 to joules....")
# 1 kWh = 3.6 x 10^6 joules
KWH_TO_J = 3.6e6
E_blue_j = blue_W_m2_280_4000 * KWH_TO_J #convert kWh to joules
E_green_j = green_W_m2_280_4000 * KWH_TO_J
E_red_j = red_W_m2_280_4000 * KWH_TO_J
print(f"E_blue_j: {E_blue_j} J/m2")
print(f"E_green_j: {E_green_j} J/m2")
print(f"E_red_j: {E_red_j} J/m2")
print("\nConversion Step 1 complete.\n")

#Step 2: convert joules to NUMBER of PHOTONS
print("Conversion Step 2: joules to NUMBER of PHOTONS....")
#Energy of one photon = h * c / wavelength (in meters)
#Constants
H = 6.62607015e-34  # Planck's constant (Joule seconds)
C = 3e8 # Speed of light (m/s)
#Energy of one photon at 450nm (blue)
wl_blue_m = 450e-9  # Wavelength in meters (450 nm)
E_photon_blue = H * C / wl_blue_m  # Energy per photon (Joules)
print(f"E_photon_blue: {E_photon_blue} J")
#Energy of one photon at 550nm (green)
wl_green_m = 550e-9
E_photon_green = H * C / wl_green_m
print(f"E_photon_green: {E_photon_green} J")
#energy of one photon at 650nm (red)
wl_red_m = 650e-9
E_photon_red = H * C / wl_red_m
print(f"E_photon_red: {E_photon_red} J")
print("\nConversion Step 2 complete. \n")

#Step 3: convert Number of photons to moles of photons (mol m2)
#convert to number of photons (mol m2)
print("Conversion Step 3: NUMBER of PHOTONS to moles of photons....")
# Number of photons = total energy / energy per photon
N_blue_photons = E_blue_j / E_photon_blue  # Number of photons per square meter (blue)
N_green_photons = E_green_j / E_photon_green # Number of photons per square meter (green)
N_red_photons = E_red_j / E_photon_red # Number of photons per square meter (red)
print(f"N_blue_photons: {N_blue_photons:.2e} photons/m2")
print(f"N_green_photons: {N_green_photons:.2e} photons/m2")
print(f"N_red_photons: {N_red_photons:.2e} photons/m2")
print("\nConversion Step 3 complete. \n")

#Step 4: convert number of photons to PPFD (umol/m2/s)
print("Conversion Step 4: NUMBER of PHOTONS to PPFD (umol/m2/s)....")
#Step 4.1: convert number of photons to moles of photons
# Avogadro's number 6.022 x 10^23 photons/mole
NA = 6.022e23  # Avogadro's number (photons per mole)
N_blue_mol = N_blue_photons / NA  # Convert to moles of photons (blue)
N_green_mol = N_green_photons / NA # Convert to moles of photons (green)
N_red_mol = N_red_photons / NA # Convert to moles of photons (red)
print(f"N_blue_mol: {N_blue_mol:.6f} mol/m2") 
print(f"N_green_mol: {N_green_mol:.6f} mol/m2")
print(f"N_red_mol: {N_red_mol:.6e} mol/m2")
print()
#Step 4.2: convert moles of photons to umol/m2/s
# 1 mol = 1e6 umol; 1 hour = 3600 seconds
MOL_UMOL = 1e6/3600 # 10^6 umol / 3600s
PPFD_blue = N_blue_mol * MOL_UMOL  # Convert to umol/m2/s
PPFD_green = N_green_mol * MOL_UMOL # Convert to umol/m2/s
PPFD_red = N_red_mol * MOL_UMOL # Convert to umol/m2/s
print("\nPPFD RESULTS PER BAND: \n")
print(f"PPFD_blue: {PPFD_blue:.2f} umol/m2/s")
print(f"PPFD_green: {PPFD_green:.2f} umol/m2/s")
print(f"PPFD_red: {PPFD_red:.2f} umol/m2/s")
print("\nConversion Step 4 complete. \n")

#sum ppfd values to get total ppfd
PPFD_total = PPFD_blue + PPFD_green + PPFD_red
print(f"Total PPFD (Blue + Green + Red): {PPFD_total:.2f} umol/m2/s")