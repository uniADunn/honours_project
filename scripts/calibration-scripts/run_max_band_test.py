import os
import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import mysql.connector
from mysql.connector import Error
import paho.mqtt.client as mqtt

from shared.spectral_conversion import bands_wm2_from_payload

LED_COLOUR = "BLUE ONLY"

# MQTT Configuration
MQTT_HOST = os.getenv('MQTT_HOST')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_TOPIC = os.getenv('MQTT_TOPIC')

MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')

# MySQL Configuration
MYSQL_HOST = os.getenv('MYSQL_HOST', '127.0.0.1')
MYSQL_PORT = int(os.getenv('MYSQL_PORT','3306'))
MYSQL_DB = os.getenv('MYSQL_DB','crop_lighting')
TABLE = os.getenv('TABLE','light_readings')
DECISION_TABLE = os.getenv('DECISION_TABLE','spectral_band_decisions')

MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')

INSERT_SQL = """
INSERT INTO band_max_calibration_runs(
    cal_run_id, cal_run_seq, ts_utc,
    led_colour, zone, source, 
    slot_start_utc, slot_end_utc,
    sample_interval_s, notes,
    blue_measured_W_m2, blue_accumulated_J_m2, blue_predicted_J_m2,
    green_measured_W_m2, green_accumulated_J_m2, green_predicted_J_m2,
    red_measured_W_m2, red_accumulated_J_m2, red_predicted_J_m2,
    ) VALUES (
    %S, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s
    )
"""

# helper functions
def parse_ts_utc(ts_str: str) -> datetime:
    try:
        ts_str = (ts_str or "").strip()
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")

        ts_dt = datetime.fromisoformat(ts_str)
        return ts_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def run_max_bands_test(led_colour: str, notes: str | None=None):

    led_colour = LED_COLOUR.strip().upper()
    if led_colour not in {"BLUE ONLY", "GREEN ONLY", "RED ONLY"}:
        raise ValueError(f"LED colour must be one of 'BLUE ONLY', 'GREEN ONLY', 'RED ONLY'. Got: {led_colour}")
    
    cal_run_id = uuid.uuid4().hex
    seq = 0

    blue_j = 0.0
    green_j = 0.0
    red_j = 0.0

    last_ts = None
    slot_start = None
    slot_end = None

    conn = mysql.connector.connect(
        host = MYSQL_HOST,
        port = MYSQL_PORT,
        user = MYSQL_USER,
        password = MYSQL_PASSWORD,
        database = MYSQL_DB,
        autocommit = True
    )
    cur = conn.cursor()

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc != 0:
            print(f"[MQTT] connect failed with reason code: {rc}")
            sys.exit(1)
        print(f"[MQTT] Connected. Subscribing to topic: {MQTT_TOPIC}")
        client.subscribe(MQTT_TOPIC)

    def on_message(client, userdata, msg):
        raw = msg.payload.decode("utf-8", errors="replace")

        try:
            data = json.loads(raw)
        except:
            return
        
        if data.get("type") == "DECISION":
            return
        
        ts_in = data.get("ts")
        source = data.get("source")
        zone = data.get("zone")

        if not ts_in or source is None or not zone:
            return
        
        ts_mysql = parse_ts_utc(ts_in)
        if ts_mysql is None:
            return
        
        if slot_start is None:
            slot_start = ts_mysql.replace(minute=0, seccond=0, microsecond=0)
            slot_end = slot_start +timedelta(hours=1)
            print(f"[CALIBRATION] cal run id: {cal_run_id}")
            print(f"[CALIBRATION] window {slot_start} -> {slot_end}")

        if ts_mysql >= slot_end:
            print(f"[CALIBRATION] hour complete")
            client.disconnect()
            return
        
        bands = bands_wm2_from_payload(data)

        blue_W = bands.get("blue_W_m2")
        green_W = bands.get("green_W_m2")
        red_W = bands.get("red_W_m2")

        if last_ts is None:
            dt_s = 1.0
        else:
            dt = (ts_mysql - last_ts).total_seconds()
            dt_s = dt if 0 < dt < 60 else 1.0

        if blue_W:
            blue_j += float(blue_W) * dt_s
        if green_W:
            green_j += float(green_W) * dt_s
        if red_W:
            red_j += float(red_W) * dt_s

        remaining_s = (slot_end - ts_mysql).total_seconds()

        blue_pred = blue_j + (float(blue_W) * remaining_s) if blue_W else None
        green_pred = green_j + (float(green_W) * remaining_s) if green_W else None
        red_pred = red_j + (float(red_W) * remaining_s) if red_W else None

        cur.execute(INSERT_SQL,
                    (cal_run_id,seq, ts_mysql, led_colour, zone, source, slot_start, slot_end,
                     dt_s, notes, blue_W, blue_j, blue_pred,
                     green_W, green_j, green_pred,
                     red_W, red_j, red_pred),
                     )
        if seq % 30 == 0:
            print(
                f"[CALIBRATION] seq: {seq} "
                f" blue_j: {blue_j:.2f}, green_j: {green_j:.2f}, red_j: {red_j:.2f} "                
            )
        seq += 1
        last_ts = ts_mysql

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT}")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()

    print(f"_"*12 + " Final totals ({led_colour}) [J/m2 per hour] " +"_"*12)
    print(f"BLUE: {blue_j:.2f} J/m2")
    print(f"GREEN: {green_j:.2f} J/m2")
    print(f"RED: {red_j:.2f} J/m2")

    cur.close()
    conn.close()

    return cal_run_id

def main():
    notes = "desk LED 3-5inch from sensor, max brightness, 1 hour duration"
    run_max_bands_test(LED_COLOUR, notes)

    print("Calibration run Complete.")