# IoT-Based Dynamic Lighting Control for Controlled Environment Agriculture

**Honours Project — BSc (Hons) Software Development**  
**Glasgow Caledonian University, 2025/26**  
**Author:** Ashley Dunn | **Supervisor:** Muhammad Ayub Ansari

---

## Overview

Indoor hydroponic systems typically rely on fixed lighting schedules that deliver the right quantity of light but not the natural daily patterns of real sunlight. This project addresses that gap by designing, building, and validating an IoT pipeline capable of reproducing historical solar diurnal spectral energy profiles indoors — targeting controlled environment agriculture (CEA) applications.

Rather than using static lighting recipes, the system derives hourly spectral energy targets from real NASA solar irradiance data and evaluates three actuator control models against those targets under both scaled and unscaled hardware conditions.

---

## System Architecture

```
Raspberry Pi 5                    Windows Laptop
┌─────────────────┐               ┌──────────────────────────────────┐
│  AS7341 sensor  │               │  incoming_sensor_payload.py      │
│  BH1750 sensor  │               │  (MQTT subscriber + MySQL writer)│
│  pi_manager.py  │──── MQTT ────▶│                                  │
│  (systemd svc)  │  (Netbird     │  run_light_schedule.py           │
│                 │   overlay)    │  (scheduler + actuator models)   │
└─────────────────┘               │                                  │
                                  │  MySQL: crop_lighting database   │
                                  └──────────────────────────────────┘
```

- **Sensing layer:** AS7341 11-channel spectral sensor + BH1750 lux meter over I2C on a Raspberry Pi 5
- **Communication:** Mosquitto MQTT broker; Pi and laptop connected via Netbird encrypted peer-to-peer overlay (no port forwarding required)
- **Ingestion:** Python subscriber validates payloads and writes sensor readings + control decisions to MySQL
- **Scheduling:** Run scheduler creates experiment runs, fetches solar targets from database, publishes START/STOP commands to the Pi, and runs simulation models
- **Pi service:** Deployed as a systemd service for automatic startup and restart on reboot

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3 |
| Messaging | Paho MQTT (paho-mqtt 2.1.0) |
| Database | MySQL (mysql-connector-python 9.5.0) |
| Networking overlay | Netbird |
| Sensors | AS7341 (spectral), BH1750 (lux) |
| Hardware | Raspberry Pi 5 |
| Solar data | NASA POWER API |
| Spectral reference | ASTM G173-03 |
| Crop yield reference | FAOSTAT |
| Service management | systemd (Pi), Windows batch supervisor (laptop) |
| Config | python-dotenv |

---

## Repository Structure

```
honours_project/
├── scripts/
│   ├── backend-scripts/
│   │   ├── incoming_sensor_payload.py   # MQTT subscriber + MySQL ingestion
│   │   └── run_light_schedule.py        # Run scheduler + actuator simulations
│   ├── calibration-scripts/
│   │   └── run_max_band_test.py         # Hardware max-output calibration
│   └── pi-scripts/
│       └── pi_manager.py                # Pi sensor publisher + closed-loop control
├── shared/
│   ├── spectral_conversion.py           # AS7341 counts → W/m² conversion
│   └── simulate_control_tracking.py     # Three actuator model simulations
├── logs/                                # Runtime logs (gitignored)
├── .env.example                         # Environment variable template
├── requirements.txt
├── start_ingestion_forever.bat          # Windows supervisor script
└── start_ingestion_minimized.bat        # Startup launcher
```

---

## Setup

### Prerequisites

- Python 3.10+
- MySQL server (local or remote)
- Mosquitto MQTT broker
- Netbird (for Pi ↔ laptop connectivity)
- Raspberry Pi 5 with AS7341 and BH1750 wired over I2C (Pi-side only)

### 1. Clone and install dependencies

```bash
git clone https://github.com/uniADunn/honours_project.git
cd honours_project
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

All MQTT topics, credentials, and database connection details are configured via `.env`. The Pi-side topics in `pi_manager.py` will fall back to the defaults defined in the script if not set in `.env` — it is recommended to set them explicitly so both sides match.

### 3. Set up the database

**Note:** The full SQL schema was not retained. The table structures can be reconstructed from the INSERT statements in `scripts/backend-scripts/incoming_sensor_payload.py` and `run_light_schedule.py`.

Required tables:

- `light_readings` — raw sensor readings per sample
- `spectral_band_decisions` — closed-loop control decisions per slot
- `runs` — run metadata (crop, country, year, schedule window)
- `ref_spectral_hourly` — hourly spectral energy targets (see Data Pipeline below)
- `actuator_tracking_sim` — simulation output rows
- `band_max_calibration_runs` — hardware calibration data

### 4. Run the backend ingestion (laptop)

```bash
python scripts/backend-scripts/incoming_sensor_payload.py
```

#### Auto-start on Windows (optional)

To have the ingestion service start automatically when the machine boots:

1. Press `Win + R`, type `shell:startup`, press Enter
2. Copy `start_ingestion_minimized.bat` into that folder
3. `start_ingestion_forever.bat` must remain in the project root — the minimized launcher calls it from there
4. Update the `PROJECT_DIR` path at the top of both `.bat` files to match your machine

### 5. Run the Pi sensor publisher (Raspberry Pi)

```bash
python scripts/pi-scripts/pi_manager.py
```

To deploy as a systemd service for automatic startup, create a unit file pointing to this script.

### 6. Start a run

```bash
python scripts/backend-scripts/run_light_schedule.py
```

The scheduler will prompt for a date/time window, fetch the reference solar profile, send a START command to the Pi over MQTT, wait for the run duration, then send STOP.

---

## Data Pipeline

The `ref_spectral_hourly` table is populated by a separate data processing pipeline using:

- **NASA POWER API** — hourly solar irradiance data for a given location and year
- **FAOSTAT** — crop yield data used to identify high-performing country/year combinations
- **ASTM G173-03** — spectral fractions used to split total irradiance into blue, green, and red band targets

The data processing scripts are not included in this repository but are available on request. Without this data the scheduler has no targets to run against.

---

## Actuator Models

Three control models were implemented and evaluated, all in `shared/simulate_control_tracking.py`:

### Single Coupled Lamp (`SINGLE_LAMP`)
All spectral bands share a single PWM duty value. The band that reaches its target first caps output for all others. Structurally unable to track multiple bands independently — fails under both scaled and unscaled conditions.

### Independent Open-Loop (`INDEPENDENT_LED`)
Each band has its own duty value, calculated each step as `remaining_dose / max_possible`. No feedback — duty is recalculated from the current state at every sample. Achieves zero tracking error under scaled conditions.

### Independent Closed-Loop (`CLOSED_LOOP_INDEPENDENT_LED`)
Extends the open-loop model with tolerance-based feedback. Each band receives an INCREASE / HOLD / DECREASE decision every 5 seconds by comparing predicted end-of-hour delivery against the target within a configurable tolerance band (default ±5%). Output level adjusts in discrete steps. Maintains MAE within the 5% threshold across all three bands under scaled conditions.

---

## Key Results

| Model | Unscaled | Scaled |
|---|---|---|
| Single Coupled Lamp | Fails (structural) | Fails (structural) |
| Independent Open-Loop | Red band saturates | Zero error all bands |
| Independent Closed-Loop | Red band saturates | MAE < 5% all bands |

`SIM_SCALE_MODE` in `run_light_schedule.py` controls whether targets are scaled down to the hardware's maximum deliverable output before evaluation.

---

## Shared Module: Spectral Conversion

`shared/spectral_conversion.py` converts raw AS7341 counts to W/m² using the datasheet reference irradiance (107.67 µW/cm² = 1.0767 W/m²) and normalises for gain and integration time. Bands are grouped as:

- **Blue:** 415 nm + 445 nm + 480 nm
- **Green:** 515 nm + 555 nm + 590 nm
- **Red:** 630 nm + 680 nm

---

## Outcome

The engineering hypothesis was supported: the system can reproduce historical diurnal solar spectral energy profiles within defined hardware limits when independent per-band LED control is used and hardware output reaches or exceeds the maximum solar target. The system is ready to progress to biological validation — comparing dynamic solar-guided lighting against static recipes in a live hydroponic grow environment.

The supervisor (Muhammad Ayub Ansari, GCU) indicated interest in potential conference publication following submission.

---

## License

Academic project — Glasgow Caledonian University, 2025/26. Contact the author before reuse.
