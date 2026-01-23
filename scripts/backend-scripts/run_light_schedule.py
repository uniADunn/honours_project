import os
import json
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
import mysql.connector
import paho.mqtt.client as mqtt
import uuid

from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# load .env variables
load_dotenv()
# set up logger
def setup_logger() -> logging.Logger:
    repo_root = Path(__file__).resolve().parents[2]
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "light_schedule.log"

    logger = logging.getLogger("light_schedule")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    #console_handler
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)

    #rotating file handler (2mb per file keep 5 old files)
    fh = RotatingFileHandler(
        log_file,
        maxBytes=2*1024*1024,
        backupCount=5,
        encoding="utf-8"
    )
    fh.setFormatter(fmt)

    #prevent duplicate handlers
    if not logger.handlers:
        logger.addHandler(sh)
        logger.addHandler(fh)
    
    logger.info(f"Logger initialized. log_file={log_file}")
    return logger
# initialize logger
LOGGER = setup_logger()

# CONFIGURATION
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))

MYSQL_DB = os.getenv("MYSQL_DB", "crop_lighting")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")

if not MYSQL_PASSWORD:
    LOGGER.error("MYSQL_PASSWORD is not set in backend .env")
    raise RuntimeError("MYSQL_PASSWORD is not set")

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
    LOGGER.info(f"[RUN SCHEDULER] Created run in database. run_id: {run_id}")

# get the best country and year for entered crop
def get_best_country_year_for_crop(conn, crop: str) -> tuple[str, int]:
    LOGGER.info(f"[DB] Getting best country and year for: '{crop}'. Please wait...")
    query = """
            SELECT country, year, yield_t_ha
            FROM ref_spectral_hourly
            WHERE crop = %s
            GROUP BY country, year, yield_t_ha
            ORDER BY yield_t_ha DESC
            LIMIT 1
    """
    cur = conn.cursor()
    try:
        cur.execute(query, (crop,))
        row = cur.fetchone()
        if not row:
            LOGGER.info(f"No spectral profile found for crop: {crop}")
            raise ValueError(f"No spectral profile found for crop: {crop}")
        country, year, _yield = row
        return str(country), int(year)
    finally:
        cur.close()

def get_best_light_profile(conn, crop: str, country: str, year: int):
    LOGGER.info(f"[DB] Fetching light profile for: country- '{country}', year- '{year}'")
    print(f"[DB] Fetching light profile for: country- '{country}', year- '{year}'")
    query = """
            SELECT 
                STR_TO_DATE(
                    CONCAT(
                        year, '-',
                        LPAD(MO, 2, '0'), '-',
                        LPAD(DY, 2, '0'), ' ',
                        LPAD(HR, 2, '0'), ':00:00'),
                    '%Y-%m-%d %H:%i:%s') AS ref_ts,
                    year, mo, dy, hr,
                    blue_W_m2_280-4000, green_W_m2_280_4000, red_W_m2_280_4000
                    FROM ref_spectral_hourly
                    WHERE crop = %s
                        AND country = %s
                        AND year = %s
                    ORDER BY mo, dy, hr;
    """
    cur = conn.cursor()
    try:
        cur.execute(query, (crop, country, year,))
        row = cur.fetch()
        if not row:
            LOGGER.info("No profile found matching")
            raise ValueError("No profile found matching")
        return row
    finally:
        cur.close()

# update run status
def set_run_status(conn, run_id: str, status:str, note:str | None = None):
    if note is None:
        sql_update = "UPDATE runs SET status=%s, status_ts=UTC_TIMESTAMP(6) WHERE run_id=%s"
        args = (status, run_id)
        LOGGER.info(f"[RUN SCHEDULER]Updated run status. run_id: {run_id}, status: {status}")
    else:
        sql_update = "UPDATE runs SET status=%s, status_ts=UTC_TIMESTAMP(6), note=%s WHERE run_id=%s"
        args = (status, note, run_id)
        LOGGER.info(f"[RUN SCHEDULER] Updated run status. run_id: {run_id}, status: {status}, note: {note}")
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
            LOGGER.error("[MQTT] Error decoding payload or parsing JSON")
            return

def mqtt_publish_n_wait_ack(zone:str, payload:dict, timeout_s: int = 5):
    cmd_topic, ack_topic, _data_topic = get_topics(zone)
    waiter = AckWaiter()
    client = mqtt.Client(
        client_id = f"run_scheduler_{uuid.uuid4().hex[:8]}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        LOGGER.info("[MQTT] Using MQTT_USER and MQTT_PASSWORD from backend .env")
    else:
        LOGGER.error("[MQTT] MQTT_USER / MQTT_PASSWORD missing in backend .env")
        raise RuntimeError("[MQTT] MQTT_USER / MQTT_PASSWORD missing in backend .env")

    client.on_message = waiter.on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.subscribe(ack_topic)
    client.loop_start()

    client.publish(cmd_topic, json.dumps(payload))
    LOGGER.info(f"[MQTT] connected to MQTT broker at {MQTT_HOST}:{MQTT_PORT}, Subscribed to {ack_topic}, Publishing to {cmd_topic}, with payload: {payload}")

    #wait for ack or timeout (no reply)
    deadline = time.time() +timeout_s
    while time.time() < deadline:
        if waiter.last_ack is not None:
            ack = waiter.last_ack
            client.loop_stop()
            client.disconnect()
            LOGGER.info(f"[RUN SCHEDULER] Received ACK: {ack}")
            return ack
        time.sleep(0.05)
    client.loop_stop()
    client.disconnect()
    LOGGER.warning(f"[RUN SCHEDULER] ACK wait timed out after {timeout_s} seconds")
    return None

# run operations/commands: start/stop
def start_run(zone:str, run_id:str, sample_interval_s: int = 5):
    payload = {
        "type": "START",
        "run_id": run_id,
        "sample_interval_s": int(sample_interval_s)
    }
    LOGGER.info(f"[RUN SCHEDULER] Sending START command to zone: {zone}, run_id: {run_id}, sample_interval_s: {sample_interval_s}")
    ack = mqtt_publish_n_wait_ack(zone, payload, timeout_s=8)         
    #LOGGER.info(f"[RUN SCHEDULER] START command ACK received: {ack}")
    return ack

def stop_run(zone:str, run_id:str):
    payload = {
        "type": "STOP",
        "run_id": run_id
    }
    
    LOGGER.info(f"[RUN SCHEDULER] Sending STOP command to zone: {zone}, run_id: {run_id}")
    ack = mqtt_publish_n_wait_ack(zone, payload, timeout_s=5)

    #LOGGER.info(f"[RUN SCHEDULER] STOP command ACK received: {ack}")
    return ack

# main flow
def main():
    # set crop selected
    crop = "Tomatoes".strip()
    conn = db_connect()

    # get country and year for crop
    country, year = get_best_country_year_for_crop(conn, crop)
    print(f"\n[REF] crop: '{crop}': returns country: {country}, year: {year}")
    LOGGER.info(f"[REF] crop: '{crop}': best country: {country}, year achievied: {year}")

    # get the yearly profile from ref_spectral_hourly for country and year
    light_profile = get_best_light_profile(conn, country, year)
    for i in range (len(0,light_profile)):
        print(i)



    # # set zone
    # zone = "zone1"

    # #generate a run id (later can get a user-entered one)
    # run_id = f"testing_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


    # create run row
    # create_run(conn, run_id, note="manual test start via scheduler script")

    # #send start command to pi
    # try:
    #     ack = start_run(zone, run_id, sample_interval_s=5)
    #     if not ack or ack.get("status") != "OK":
    #         set_run_status(conn, run_id, "FAILED", note=f"Start Failed, ack: {ack}")
    #         LOGGER.error(f"[RUN SCHEDULER] Start run failed for run_id: {run_id}, ack: {ack}")
    #         #print("[START] Failed; updated runs.status: FAILED")
    #         return
    #     LOGGER.info(f"[RUN SCHEDULER] Start run succeded for run_id: {run_id}, status: RUNNING")
    #     set_run_status(conn, run_id, "RUNNING")
    # except TimeoutError as to:
    #     LOGGER.error(f"[RUN SCHEDULER] timed out waiting for ack from pi. {to}")
    #     return
    # #print(f"[RUN] Running. run_id: {run_id}.\nWaiting 15 seconds to accumulate sensor readings...")
    # #LOGGER.info(f"[RUN SCHEDULER] Run is now RUNNING for run_id: {run_id}. Waiting 15 seconds to accumulate sensor readings...")
    # run_duration = 600 # 10 mins (10 * 60 = 600)
    # try:
    #     time.sleep(run_duration) # simulate run for 10 minutes 10 * 60 = 600 seconds)
    #     ack2 = stop_run(zone, run_id)
    #     LOGGER.info(f"[RUN SCHEDULER]  Stop ACK received. {ack2}")
    #     #print("[STOP] ack: ", ack2)
    #     set_run_status(conn, run_id, "COMPLETED")
    #     LOGGER.info(f"[RUN SCHEDULER] Run completed successfully for run_id: {run_id}, status: COMPLETED")
    # except KeyboardInterrupt:
    #     LOGGER.info("[RUN SCHEDULER] Keyboard Interrupt detected, Stopping the run...")
    #     #print("Keyboard Interrupt detected, manually stopping the run...")
    #     ack2 = stop_run(zone, run_id)
    #     #print("[STOP] ack: ", ack2)
    #     set_run_status(conn, run_id, "STOPPED", note="Stopped via Keyboard Interrupt")
    #     LOGGER.info(f"[RUN SCHEDULER] Run stopped via Keyboard Interrupt for run_id: {run_id}, status: STOPPED")
    #     #print("[RUN] Stopped and status updated: STOPPED")
    # except Exception as e:
    #     LOGGER.error(f"[RUN SCHEDULER] Exception occurred: {e}, stopping the run...")
    #     #print(f"[RUN] Exception occurred: {e}, stopping the run...")
    #     ack2 = stop_run(zone, run_id)
    #     #print("[STOP] ack: ", ack2)
    #     set_run_status(conn, run_id, "FAILED", note=f"Exception occurred during run: {e}")
    #     LOGGER.info(f"[RUN SCHEDULER] Run Failed due to an exception for run_id: {run_id}, status: FAILED")
    # finally:
    #     conn.close()

if __name__ == "__main__":
    main()