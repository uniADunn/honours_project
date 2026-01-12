import os
import json
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
import mysql.connector
import paho.mqtt.client as mqtt
import uuid

#load_dotenv()
from pathlib import Path
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"   # honours_project/.env
load_dotenv(dotenv_path=ENV_PATH, override=True)
print(f"[env] loaded: {ENV_PATH}")


# CONFIGURATION
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))

MYSQL_DB = os.getenv("MYSQL_DB", "crop_lighting")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")

if not MYSQL_PASSWORD:
    raise RuntimeError("MYSQL_PASSWORD is not set")

print(f"[cfg] MQTT_HOST={MQTT_HOST} MQTT_PORT={MQTT_PORT}")
print(f"[cfg] MYSQL_HOST={MYSQL_HOST} MYSQL_PORT={MYSQL_PORT} MYSQL_DB={MYSQL_DB} MYSQL_USER={MYSQL_USER}")


# HELPER FUNCTIONS

# topic naming
def get_topics(zone: str):
    zone = str(zone).strip()
    cmd_topic = f"adunn/control/{zone}/cmd"
    ack_topic = f"adunn/control/{zone}/ack"
    data_topic = f"adunn/sensor/light/{zone}"
    return cmd_topic, ack_topic, data_topic

# mysql connection
def db_connect():
    return mysql.connector.connect(
        host = MYSQL_HOST,
        port = MYSQL_PORT,
        user = MYSQL_USER,
        password = MYSQL_PASSWORD,
        database = MYSQL_DB,
        autocommit= True
    )

# create a run
def create_run(conn, run_id:str, note:str = "created by run_light_schedule script"):
    sql_insert = """
    INSERT INTO runs(run_id, created_ts, status, status_ts, note)
    VALUES (%s, UTC_TIMESTAMP(6), %s, UTC_TIMESTAMP(6), %s)
    """
    cur = conn.cursor()
    cur.execute(sql_insert, (run_id, "STARTING", note))
    cur.close()

# update run status
def set_run_status(conn, run_id: str, status:str, note:str | None = None):
    if note is None:
        sql_update = "UPDATE runs SET status=%s, status_ts=UTC_TIMESTAMP(6) WHERE run_id=%s"
        args = (status, run_id)
    else:
        sql_update = "UPDATE runs SET status=%s, status_ts=UTC_TIMESTAMP(6), note=%s WHERE run_id=%s"
        args = (status, note, run_id)
    cur= conn.cursor()
    cur.execute(sql_update, args)
    cur.close()

# mqtt helpers and class: AckWaiter
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

def mqtt_publish_n_wait_ack(zone:str, payload:dict, timeout_s: int = 5):
    cmd_topic, ack_topic, _data_topic = get_topics(zone)
    
    waiter = AckWaiter()

    client = mqtt.Client(
        client_id = f"run_scheduler_{uuid.uuid4().hex[:8]}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )

    client.on_message = waiter.on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.subscribe(ack_topic)
    client.loop_start()

    client.publish(cmd_topic, json.dumps(payload))

    #wait for ack or timeout (no reply)
    deadline = time.time() +timeout_s
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

# run operations: start/stop
def start_run(zone:str, run_id:str, sample_interval_s: int = 5):
    payload = {
        "type": "START",
        "run_id": run_id,
        "sample_interval_s": int(sample_interval_s)
    }
    ack = mqtt_publish_n_wait_ack(zone, payload, timeout_s=8)
    return ack

def stop_run(zone:str, run_id:str):
    payload = {
        "type": "STOP",
        "run_id": run_id
    }
    ack = mqtt_publish_n_wait_ack(zone, payload, timeout_s=5)
    return ack

# main flow
def main():
    # set zone
    zone = "zone1"

    #generate a run id (later can get a user-entered one)
    run_id = f"testing_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    conn = db_connect()

    # create run row
    create_run(conn, run_id, note="manual test start via scheduler script")

    #send start command to pi
    ack = start_run(zone, run_id, sample_interval_s=5)
    print("[START] ack: ", ack)

    if not ack or ack.get("status") != "OK":
        set_run_status(conn, run_id, "FAILED", note=f"Start Failed, ack: {ack}")
        print("[START] Failed; updated runs.status: FAILED")
        return
    
    set_run_status(conn, run_id, "RUNNING")
    print(f"[RUN] Running. run_id: {run_id}.\nWaiting 15 seconds to accumulate sensor readings...")
    time.sleep(15)

    ack2 = stop_run(zone, run_id)
    print("[STOP] ack: ", ack2)

    set_run_status(conn, run_id, "STOPPED")
    print("[RUN] Stopped and status updated: STOPPED")

    conn.close()

if __name__ == "__main__":
    main()