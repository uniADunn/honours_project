import os
import sys
import json
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

import mysql.connector
import paho.mqtt.client as mqtt

from dotenv import load_dotenv
#project root and load_env
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from shared.spectral_conversion import bands_wm2_from_payload

#CALIBRATION SETTINGS HARD-CODED
ZONE = "zone1"
LED_COLOUR = "FULL SPECTRUM"
NOTES = " desk LED 2.7w WARM WHITE 4-6cm distance from sensor,"
SAMPLE_INTERVAL_S = 5

# ENV CONFIG
# mqtt configuration
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC")

MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# mysql configuration
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_DB = os.getenv("MYSQL_DB")

MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")

# MYSQL INSERT
INSERT_SQL = """
INSERT INTO band_max_calibration_runs(
    cal_run_id, cal_run_seq, ts_utc,
    led_colour, zone, source,
    slot_start_utc, slot_end_utc,
    sample_interval_s, notes,
    
    blue_measured_W_m2, blue_accumulated_J_m2, blue_predicted_J_m2,
    green_measured_W_m2, green_accumulated_J_m2, green_predicted_J_m2,
    red_measured_W_m2, red_accumulated_J_m2, red_predicted_J_m2
) VALUES (
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s
)
"""

# TOPIC HELPER
def get_mqtt_topics(zone: str) -> tuple[str, str]:
    cmd_topic = f"adunn/control/{zone}/cmd"
    ack_topic = f"adunn/control/{zone}/ack"
    return cmd_topic, ack_topic

# TIME HELPERS
def iso_utc_z_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_ts_mysql_utc(ts_str: str) -> datetime:
    try:
        ts_str = (ts_str or "").strip()
        if not ts_str:
            return None
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")
        
        dt = datetime.fromisoformat(ts_str)
        return dt.replace(tzinfo=None)
    except Exception:
        return None

# force none -> 0.0 
def safe_float(val) -> float:
    try:
        return 0.0 if val is None else float(val)
    except Exception:
        return 0.0
# CONFIG CHECK
def config_check()-> bool:
    if LED_COLOUR not in {"BLUE ONLY", "GREEN ONLY", "RED ONLY", "FULL SPECTRUM"}:
        raise ValueError(f"LED COLOUR must be one of 'BLUE ONLY', 'GREEN ONLY', 'RED ONLY', 'FULL SPECTRUM'. Got: {LED_COLOUR}")
    
    if not MQTT_HOST or not MQTT_TOPIC:
        raise RuntimeError("MQTT_HOST AND MQTT_TOPIC must be set in .env file")
        
    if not MQTT_USER or not MQTT_PASSWORD:
        raise RuntimeError("MQTT_USER and MQTT_PASSWORD must be set in .env file")
    
    if not MYSQL_DB:
        raise RuntimeError("MYSQL_DB must be set in .env file")
    return True

# build dummy targets by slot for testing, returning a list of dicts with slot number, reference timestamp, and target values for blue, green, and red channels
def build_dummy_targets_by_slot() -> list[dict]:
    now_z = iso_utc_z_now()
    return[{
        "slot": 0,
        "ref_ts": now_z,
        "target" : {
            "blue": 0.0,
            "green": 0.0,
            "red": 0.0
        }
    }]
# helper class to wait for ACK messages on the MQTT topic, storing the last received ACK and providing a method to check if it matches expected type, run_id, and zone values
class AckWaiter:
    def __init__(self):
        self.last_ack = None
    # MQTT on_message callback to decode incoming messages, parse as JSON, and store the last ACK message in the instance variable last_ack. If decoding or parsing fails, the message is ignored.
    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            data = json.loads(payload)
            self.last_ack = data
        except Exception:
            return
    
    # check if the last received ACK matches the expected type, run_id, and zone values
    def matches(self, type:str, run_id: str, zone: str) -> bool:
        if not self.last_ack:
            return False
        
    

        if str((type) or "".upper()) != str(type or "").upper():
            return False
        if str((run_id) or "".upper()) != str(run_id or "").upper():
            return False
        if str((zone) or "").strip().lower() != str(zone or "").strip().lower():
            return False
        return True
# helper function to publish a command payload to the MQTT command topic for the given zone, and wait for an ACK message on the corresponding ACK topic that matches the expected type, run_id, and zone values. The function returns the ACK message if received within the timeout period, or None if no matching ACK is received.
def publish_cmd_and_wait_for_ack(zone:str, payload:dict, timeout_s: int = 10):

    cmd_topic, ack_topic = get_mqtt_topics(zone)
    waiter = AckWaiter()

    client_id = f"calibration_script_{uuid.uuid4()}"
    client = mqtt.Client(
        client_id=client_id,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_message = waiter.on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.subscribe(ack_topic, qos=1)
    client.loop_start()
    time.sleep(0.2)


    print(f"[MQTT] Publishing cmd: {payload.get('type')} to {cmd_topic}")
    client.publish(cmd_topic, json.dumps(payload), qos=1)

    run_id = payload.get("run_id")
    deadline = time.time() + timeout_s
    ack = None

    while time.time() < deadline:
        if waiter.last_ack is not None:
            ack = waiter.last_ack
            client.loop_stop()
            client.disconnect()
            return ack
        time.sleep(0.05)

    client.loop_stop()
    client.disconnect()

    return None
# helper function to send a START command with the given zone and calibration run ID, and wait for an ACK message confirming the command was received and processed. The function returns the ACK message if received within the timeout period, or None if no ACK is received.
def send_start(zone:str, cal_run_id:str):
    payload =  {
        "type": "START",
        "run_id": cal_run_id,
        "run_start_ts": iso_utc_z_now(),
        "sample_interval_s": 5,
        "targets_by_slot": build_dummy_targets_by_slot(),
        "source": "calibration_script",
        "zone": zone
    }
    return publish_cmd_and_wait_for_ack(zone, payload, timeout_s=10)

# helper function to send a STOP command with the given zone and calibration run ID, and wait for an ACK message confirming the command was received and processed. The function returns the ACK message if received within the timeout period, or None if no ACK is received.
def send_stop(zone:str, cal_run_id:str, timeout_s: int = 10):
    payload = {
        "type": "STOP",
        "run_id": cal_run_id,
        "source": "calibration_script",
        "zone": zone
    }
    return publish_cmd_and_wait_for_ack(zone, payload, timeout_s)

# helper function to validate that the received ACK message has a status of "OK", and raise a RuntimeError with details if the ACK is missing or has an error status
def require_ok_ready(ack:dict):
    if not ack:
        raise RuntimeError("No ACK received within timeout period")
    if str(ack.get("status") or "").upper() != "OK":
        detail = ack.get("detail")
        raise RuntimeError(f"[ACK ERROR] Expected ACK with status 'OK'. Got: {ack}. Detail: {detail}")

# helper function to run a one-hour calibration session, collecting sensor data, calculating joules, and storing results in the database
def run_hour_calibration(cal_run_id: str):

    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        autocommit=True
    )
    cur = conn.cursor()

    
    cal_run_seq = 0

    blue_j, green_j, red_j = 0.0, 0.0, 0.0
    last_ts = None
    slot_start = None
    slot_end = None

    # MQTT callbacks
    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"[mqtt] connected with result code {rc}")
        if rc != 0:
            raise RuntimeError(f"failed to connect to MQTT broker. RC: {rc}")
        client.subscribe(MQTT_TOPIC, qos=1)
        print("[CAL] subscribed to sensor data topic")

    def on_message(client, userdata, msg):
        print(f"[cal] received message on topic {msg.topic}")
        nonlocal cal_run_seq, blue_j, green_j, red_j, last_ts, slot_start, slot_end

        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            return
        
        if data.get("type") == "DECISION":
            print(f"[DECISION] ignoring received decision: {data}")
            return
        
        ts_in = data.get("ts")
        source = data.get("source")
        zone = data.get("zone")
        if not ts_in or source is None or zone is None:
            return
        
        ts_mysql = parse_ts_mysql_utc(ts_in)
        if ts_mysql is None:
            return
        
        if slot_start is None:
            slot_start = ts_mysql
            slot_end = slot_start + timedelta(hours=1)
            print(f"[cal] window: {slot_start} -> {slot_end}")

        if ts_mysql >= slot_end:
            print(f"[cal] hour complete")
            client.disconnect()
            return
        
        bands = bands_wm2_from_payload(data)
        blue_w = safe_float(bands.get("blue_W_m2"))
        green_w = safe_float(bands.get("green_W_m2"))
        red_w = safe_float(bands.get("red_W_m2"))

        if last_ts is None:
            dt_s = 0.0
            blue_j = blue_w * 3600.0
            green_j = green_w * 3600.0
            red_j = red_w * 3600.0
        else:
            dt = (ts_mysql - last_ts).total_seconds()
            dt_s = dt if 0 < dt < 60 else SAMPLE_INTERVAL_S

        blue_j += blue_w * dt_s
        green_j += green_w * dt_s
        red_j += red_w * dt_s

        remaining_s = (slot_end-ts_mysql).total_seconds()

        blue_pred_j = blue_j + (blue_w * remaining_s)
        green_pred_j = green_j + (green_w * remaining_s)
        red_pred_j = red_j + (red_w * remaining_s)

        try:
            cur.execute(
                INSERT_SQL,
                (
                    cal_run_id, cal_run_seq, ts_mysql,
                    LED_COLOUR, zone, source,
                    slot_start, slot_end,
                    dt_s, NOTES,

                    blue_w, blue_j, blue_pred_j,
                    green_w, green_j, green_pred_j,
                    red_w, red_j, red_pred_j
                )
            )
        except Exception as e:
            print(f"[DB ERROR] insert failed: {repr(e)}")

        if cal_run_seq % 5 == 0:
            print(f"[cal] inserted seq {cal_run_seq} at ts {ts_mysql}")
        cal_run_seq += 1
        last_ts = ts_mysql
    
    client = mqtt.Client(
        client_id =  f"calibration_{uuid.uuid4().hex[:8]}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()

    print("\nFINAL TOTALS:")
    print(f"blue J: {blue_j:.4f}")
    print(f"green J: {green_j:.4f}")
    print(f"red J: {red_j:.4f}")

    cur.close()
    conn.close()


if __name__ == "__main__":
   
    if not config_check():
        print("Configuration check failed. Please fix the issues and try again.")
        sys.exit(1)
    
    cal_run_id = f"cal_test_run_{uuid.uuid4().hex[:8]}"

    ack = send_start(ZONE, cal_run_id)
    print(f"[READY ACK] RECEIVED ACK: {ack}")
    try:
        print(f"entering run_hour_calibration")
        run_hour_calibration(cal_run_id)

        ack2 = send_stop(ZONE, cal_run_id)
        print(f"[STOP ACK] RECEIVED ACK: {ack2}")
    except KeyboardInterrupt:
        print("Calibration interrupted by user.")
    except Exception as e:
        print(f"[ERROR] run_hour_calibration failed: {repr(e)}")

    
    

    
    
