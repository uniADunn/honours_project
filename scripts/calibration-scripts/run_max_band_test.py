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
LED_COLOUR = "BLUE ONLY"
NOTES = " desk LED strip, max brightness, 3-5cm distance from sensor,"
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
        if ts_str.endswit("Z"):
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

def config_check():
    if LED_COLOUR not in {"BLUE ONLY", "GREEN ONLY", "RED ONLY"}:
        raise ValueError(f"LED COLOUR must be one of 'BLUE ONLY', 'GREEN ONLY', 'RED ONLY'. Got: {LED_COLOUR}")
    
    if not MQTT_HOST or not MQTT_TOPIC:
        raise RuntimeError("MQTT_HOST AND MQTT_TOPIC must be set in .env file")
        
    if not MQTT_USER or not MQTT_PASSWORD:
        raise RuntimeError("MQTT_USER and MQTT_PASSWORD must be set in .env file")
    
    if not MYSQL_DB:
        raise RuntimeError("MYSQL_DB must be set in .env file")
    
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
    
class AckWaiter:
    def __init__(self):
        self.last_ack = None

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            data = json.loads(payload)
            self.last_ack = data
        except Exception:
            return
        
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
    
def publish_cmd_and_wait_for_ack(zone:str, payload:dict, type:str, timeout_s: int = 10):

    cmd_topic, ack_topic = get_mqtt_topics(zone)
    waiter = AckWaiter()

    client_id = f"calibration_script_{uuid.uuid4()}"
    client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_message = waiter.on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.subscribe(ack_topic, qos=1)
    client.loop_start()

    print(f"[MQTT] Publishing cmd: {payload.get('type')} to {cmd_topic}")
    client.publish(cmd_topic, json.dumps(payload), qos=1)

    run_id = payload.get("run_id")
    deadline = time.time() + timeout_s
    ack = None

    while time.time() < deadline:
        if waiter.matches(type=type, run_id=run_id, zone=zone):
            ack = waiter.last_ack
            break
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()

    return ack

def send_start(zone:str, run_id:str, sample_interval_s: int = 5):
    payload =  {
        "type": "START",
        "run_id": run_id,
        "run_start_ts": iso_utc_z_now(),
        "sample_interval_s": sample_interval_s,
        "targets_by_slot": build_dummy_targets_by_slot(),
        "source": "calibration_script",
        "zone": zone
    }
    return publish_cmd_and_wait_for_ack(zone, payload, type="START", timeout_s=10)

def require_ok_ready(ack:dict):
    if not ack:
        raise RuntimeError("No ACK received within timeout period")
    if str(ack.get("status") or "").upper() != "OK":
        detail = ack.get("detail")
        raise RuntimeError(f"[ACK ERROR] Expected ACK with status 'OK'. Got: {ack}. Detail: {detail}")
    
# Print out the loaded environment variables to verify they are being read correctly (TESTING PURPOSES) (DELETE THIS LATER)
if __name__ == "__main__":
    config_check()
    
    run_id = f"max_band_test_{uuid.uuid4().hex[:8]}"
    print(f"run_id: {run_id}")

    ack = send_start(ZONE, run_id, SAMPLE_INTERVAL_S)
    print(f"[READY ACK] RECEIVED ACK: {ack}")

    require_ok_ready(ack)
    print("[START] OK")
    
