import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
import mysql.connector
import paho.mqtt.client as mqtt
import uuid

from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from shared.simulate_control_tracking import simulate_target_by_slot

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

# Keep DB note writes bounded to avoid MySQL "Data too long" errors.
RUN_NOTE_MAX_LEN = 255

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

RUN_MODE = "SIMULATION" # SIMULATION or REAL
CAPS_J_PER_HOUR = {
    "blue": 728357.7226,
    "green": 422294.8749,
    "red": 129824.7369,
}

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
    decision_topic = f"adunn/control/{zone}/decision"
    return cmd_topic, ack_topic, data_topic, decision_topic

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

#parse user entered date time (mm-dd hh:00)
def parse_window_dates_input(year:int)-> tuple[datetime, datetime]:
    for attempts in range(1, 6):
        start_date_raw = input(f"enter start date and hour (mm-dd hh) for year: {year}-")
        end_date_raw = input(f"enter end date and hour (mm-dd hh) for year: {year}-")
    
        try:
            start_ts = datetime.strptime(f"{year}-{start_date_raw}:00:00", "%Y-%m-%d %H:%M:%S")
            end_ts = datetime.strptime(f"{year}-{end_date_raw}:00:00", "%Y-%m-%d %H:%M:%S")

            if end_ts <= start_ts:
                raise ValueError(f"End ts must be after start ts. start: {start_ts}, end: {end_ts}")

            return start_ts, end_ts
        except ValueError as e:
            remaining = 5 - attempts
            print(
                f"Bad input. use MM-DD HH (e.g 06-01 13) "
                f"Attempt {attempts}/5 "
                f"{'Remaining: ' + str(remaining) if remaining else 'No attempts left '}\n"
                f"Details: {e}\n"
            )
    raise ValueError("Invalid date input aborting...")
            
#slice helper
def slice_profile(light_profile, start_ts:datetime, end_ts:datetime):
    sliced = [row for row in light_profile if start_ts <= row[0] <= end_ts]
    if not sliced:
        LOGGER.warning(f"No profile rows found in slice {start_ts} -> {end_ts}")
        raise ValueError(f"No profile rows found in slice {start_ts} -> {end_ts}")
    return sliced

# get the duration of the slice
def get_duration_of_slice(start, end)-> int:
    duration = end - start
    duration_in_seconds = duration.total_seconds()
    if duration_in_seconds < 0:
        raise ValueError(f"Invalid Window: start: {start}, end: {end}, seconds: {duration_in_seconds}")
    return int(duration_in_seconds)

# create a run
def create_run_with_metadata(conn, run_id:str, status:str, crop:str, ref_country:str, ref_year:int, schedule_start_ts, schedule_end_ts, sample_interval_s:int, zone:str, note:str | None = None)-> None:
    if note is None:
        sql_insert = """
        INSERT INTO runs(run_id, created_ts, status, status_ts, crop, ref_country, ref_year, schedule_start_ts, schedule_end_ts, sample_interval_s, zone)
        VALUES (%s, UTC_TIMESTAMP(6), %s, UTC_TIMESTAMP(6), %s, %s, %s, %s, %s, %s, %s)
        """
        args = (run_id, status, crop, ref_country, ref_year, schedule_start_ts, schedule_end_ts, sample_interval_s, zone)
        LOGGER.info("no note")
    else:
        sql_insert = """
            INSERT INTO runs(run_id, created_ts, status, status_ts, note, crop, ref_country, ref_year, schedule_start_ts, schedule_end_ts, sample_interval_s, zone)
            VALUES (%s, UTC_TIMESTAMP(6), %s, UTC_TIMESTAMP(6), %s, %s, %s, %s, %s, %s, %s, %s)
        """
        args = (run_id, status, note, crop, ref_country, ref_year, schedule_start_ts, schedule_end_ts, sample_interval_s, zone)
        LOGGER.info("got a note")
    cur = conn.cursor()
    try:
        cur.execute(sql_insert, args)
        conn.commit()
    finally:
        cur.close()
        
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
    #print(f"[DB] Fetching light profile for: country- '{country}', year- '{year}'")
    query = """
            SELECT 
                CAST(
                    CONCAT(
                        year, '-',
                        LPAD(MO, 2, '0'), '-',
                        LPAD(DY, 2, '0'), ' ',
                        LPAD(HR, 2, '0'), ':00:00')
                    AS DATETIME(6)) AS ref_ts,
                    year, mo, dy, hr,
                    blue_MJ_m2_hr_280_4000, green_MJ_m2_hr_280_4000, red_MJ_m2_hr_280_4000
                    FROM ref_spectral_hourly
                    WHERE UPPER(crop) = UPPER(%s)
                        AND UPPER(country) = UPPER(%s)
                        AND year = %s
                    ORDER BY mo, dy, hr;
    """
    cur = conn.cursor()
    try:
        cur.execute(query, (crop, country, year))
        rows = cur.fetchall()
        if not rows:
            LOGGER.info("No profile found matching")
            raise ValueError("No profile found matching")
        return rows
    except Exception as e:
        LOGGER.error(f"[DB] Failed fetching light profile because: {e}")
        return 
    finally:
        cur.close()

# update run status
def set_run_status(conn, run_id: str, status:str, note:str | None = None):
    safe_note = None
    if note is not None:
        safe_note = str(note)
        if len(safe_note) > RUN_NOTE_MAX_LEN:
            LOGGER.warning(
                f"[RUN SCHEDULER] Truncating note for run_id={run_id} "
                f"from {len(safe_note)} to {RUN_NOTE_MAX_LEN} chars"
            )
            safe_note = safe_note[:RUN_NOTE_MAX_LEN]

    if note is None:
        sql_update = "UPDATE runs SET status=%s, status_ts=UTC_TIMESTAMP(6) WHERE run_id=%s"
        args = (status, run_id)
        LOGGER.info(f"[RUN SCHEDULER]Updated run status. run_id: {run_id}, status: {status}")
    else:
        sql_update = "UPDATE runs SET status=%s, status_ts=UTC_TIMESTAMP(6), note=%s WHERE run_id=%s"
        args = (status, safe_note, run_id)
        LOGGER.info(f"[RUN SCHEDULER] Updated run status. run_id: {run_id}, status: {status}, note: {safe_note}")
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
# publish a command to the pi and wait for an ack response on the ack topic, return the ack or None if timeout
def mqtt_publish_n_wait_ack(zone:str, payload:dict, timeout_s: int = 5):
    cmd_topic, ack_topic, _data_topic, decision_topic = get_topics(zone)
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
# start a run by sending the START command to the pi with the run details and target slots, return the ack response from the pi
def start_run(
        zone:str,
        run_id:str,
        sample_interval_s: int,
        run_start_ts: datetime,
        ref_meta: dict,
        ref_start_ts: datetime,
        targets_by_slot: list,
        compare_mode: str,
        decision_policy:dict | None = None
    ):
    
    if decision_policy is None:
        decision_policy = {
            "tolerance_pct": 5.0,
            "min_samples_before_decision": 6
        }

    payload = {
        "type": "START",
        "run_id": run_id,
        "sample_interval_s": int(sample_interval_s),

        #time anchors
        "run_start_ts": run_start_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ref_start_ts": ref_start_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),

        #metadata
        "ref_meta": ref_meta, # e.g. {"crop": "tomatoes", "country": "Belgium", "year": 2020}

        #comparison details
        "compare_mode": compare_mode, # cumulative
        "decision_policy": decision_policy,

        #target sequence for the run
        #each element corresponds to slot index = floor(elapsed_s/3600)
        "targets_by_slot": targets_by_slot
    }
    LOGGER.info(f"[SCHEDULER] Sending START  command to zone: {zone}, run_id: {run_id}")
    ack = mqtt_publish_n_wait_ack(zone, payload, timeout_s = 8)
    return ack
# build the target slots for the run from the sliced profile rows, each slot corresponds to 1 hour of elapsed time in the run, and includes the target blue, green, red and total light values for that hour based on the reference profile.
def build_target_slots(sliced_rows: list[tuple])->list[dict]:
    """
    sliced rows are tuples from get_best_light_profile() method
    ref_ts = index 0
    blue_MJ_m2_hr_280_4000 = index 5
    green_MJ_m2_hr_280_4000 = index 6
    red_MJ_m2_hr_280_4000 = index 7
    """
    targets: list[dict] = []

    for i, r in enumerate(sliced_rows):
        ref_ts: datetime = r[0]
        blue = float(r[5])
        green = float(r[6])
        red = float(r[7])
        total = blue + green + red


        targets.append({
            "slot": i,
            "ref_ts": ref_ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
            "target":{
                "blue": blue,
                "green": green,
                "red": red,
                "total": total
            }
        })
    return targets
# stop a run by sending the STOP command to the pi with the run_id, return the ack response from the pi
def stop_run(zone:str, run_id:str):
    payload = {
        "type": "STOP",
        "run_id": run_id
    }
    
    LOGGER.info(f"[RUN SCHEDULER] Sending STOP command to zone: {zone}, run_id: {run_id}")
    ack = mqtt_publish_n_wait_ack(zone, payload, timeout_s=5)

    #LOGGER.info(f"[RUN SCHEDULER] STOP command ACK received: {ack}")
    return ack
# for testing purposes we can hardcode the crop, zone, and sample interval
def get_hardcoded_crop_zone_sample_interval()->tuple[str,str,int]:
    crop = "Tomatoes".strip()
    zone = "zone1"
    sample_interval_s = 5
    print()
    LOGGER.info(f"[SCHEDULER] hardcoded values - crop: {crop}, zone: {zone}, sample_interval_s: {sample_interval_s}")
    return crop, zone, sample_interval_s

def insert_actuator_tracking_sim(conn, run_id:str, targets_by_slot: list, sim_rows: list, batch_size: int = 5000):
    sql_insert = """
    INSERT INTO actuator_tracking_sim(
    run_id, model, slot, t_offset_s,
    target_blue_j, target_green_j, target_red_j,
    duty_single, duty_blue, duty_green, duty_red,
    accum_blue_j, accum_green_j, accum_red_j
    )
    VALUES(
    %(run_id)s, %(model)s, %(slot)s, %(t_offset_s)s,
    %(target_blue_j)s, %(target_green_j)s, %(target_red_j)s,
    %(duty_single)s, %(duty_blue)s, %(duty_green)s, %(duty_red)s,
    %(accum_blue_j)s, %(accum_green_j)s, %(accum_red_j)s
    )
    ON DUPLICATE KEY UPDATE
    duty_single= VALUES(duty_single),
    duty_blue= VALUES(duty_blue),
    duty_green= VALUES(duty_green),
    duty_red= VALUES(duty_red),
    accum_blue_j= VALUES(accum_blue_j),
    accum_green_j= VALUES(accum_green_j),
    accum_red_j= VALUES(accum_red_j)
"""
    slot_targets = {
        s["slot"]: s["target"]
        for s in targets_by_slot
    }
    db_rows = []

    for r in sim_rows:
        slot = r["slot"]
        target = slot_targets[slot]

        db_rows.append({
            "run_id": run_id,
            "model": r["model"],
            "slot": slot,
            "t_offset_s": r["t_offset_s"],

            "target_blue_j": target["blue"] * 1000000, # convert MJ to J
            "target_green_j": target["green"] * 1000000,
            "target_red_j": target["red"] * 1000000,

            "duty_single": r["duty_single"],
            "duty_blue": r["duty_blue"],
            "duty_green": r["duty_green"],
            "duty_red": r["duty_red"],

            "accum_blue_j": r["accum_blue"],
            "accum_green_j": r["accum_green"],
            "accum_red_j": r["accum_red"],
        })

    cur = conn.cursor()
    try:
        for i in range(0, len(db_rows), batch_size):
            cur.executemany(sql_insert, db_rows[i:i+batch_size])
        conn.commit()
    finally:
        cur.close()

    return len(db_rows)

def real_run():
    crop, zone, sample_interval_s = get_hardcoded_crop_zone_sample_interval()

    #get connection to db
    conn = db_connect()

    #-------------get reference profile details: country, year, light profile for best country-year----------------#

    # get the country and year that achieved highest yield for crop
    try:
        #return the reference country and year for giving crop
        ref_country, ref_year = get_best_country_year_for_crop(conn, crop)
        LOGGER.info(f"[REF] crop: '{crop}': best country: {ref_country}, year achievied: {ref_year}")
    except Exception as e:
        LOGGER.error(f"[REF] Unable to retrieve country and year for '{crop}'")
        raise

    # retrieve the full year light profile for country, year and crop
    try:
        light_profile = get_best_light_profile(conn, crop, ref_country, ref_year)

        schedule_start_ts = light_profile[0][0]
        schedule_end_ts = light_profile[-1][0]
        
        count = len(light_profile)
        #LOGGER.info(f"[RUN SCHEDULER] Retrieved light profile successfully. count: {count}, first_ts: {schedule_start_ts}, last_ts={schedule_end_ts}")
    except Exception as e:
        #LOGGER.error(f"[RUN SCHEDULER] Unable to retrieve light profile: Ending Run...")
        raise Exception("[RUN SCHEDULER] Unable to retrieve light profile")
    
    print()
    LOGGER.info(f"[SCHEDULER] Retrieved light profile: count: {count}, first_ts: {schedule_start_ts}, last_ts={schedule_end_ts}")

    #-------------get schedule window from user input and slice the profile------------------#
    # #get date input from user
    try:
        start_ts, end_ts = parse_window_dates_input(ref_year)
    except Exception as e:
        raise
    
    print()
    LOGGER.info(f"[SCHEDULER] got start and end dates from user: start: {start_ts}, end: {end_ts}")

    try:
        slice_rows = slice_profile(light_profile, start_ts, end_ts)
        LOGGER.info(f"[RUN SCHEDULER] slice successful start: {start_ts} -> {end_ts}. rows: {len(slice_rows)}")
        duration_in_seconds = get_duration_of_slice(start_ts, end_ts)
        LOGGER.info(f"the duration of time slice in seconds: {duration_in_seconds}s")
        LOGGER.info(f"the duration of time slice with 5 seconds added on: seconds: {duration_in_seconds + 5}s")
    except ValueError as ve:
        LOGGER.error(f"[RUN SCHEDULER] slice was unsuccessful for start: {start_ts} -> {end_ts}")
        raise ve
    
    #-------------create a run in db with metadata and status CREATED------------------#
    # #generate a run id
    run_id = f"testing_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # create a run row (includes meta data, e.g. crop, country year, schedule start, schedule end, etc)
    try:
        create_run_with_metadata(conn, run_id, "CREATED", crop, ref_country, ref_year, start_ts, end_ts, sample_interval_s, zone, note=f"created by run scheduler")
        LOGGER.info(f"Run created successfully. run_id: {run_id}, crop: {crop}, country: {ref_country}, year: {ref_year}, total rows: {len(slice_rows)}")
    except Exception as e:
        LOGGER.error(f"Unable to create run: {e}")

    #-------------send start command to pi with run details and target slots------------------#
    #send start command to pi
    try:
        #build targets slots from sliced rows
        targets_slots = build_target_slots(slice_rows)

        #anchor for slot time
        run_start_ts = datetime.now(timezone.utc)

        ref_meta = {"crop": crop, "country": ref_country, "year": ref_year}
        ref_start_ts = start_ts.replace(tzinfo=timezone.utc)
        ack = start_run(
            zone, 
            run_id,
            sample_interval_s,
            run_start_ts,
            ref_meta,
            ref_start_ts,
            targets_slots,
            compare_mode="cumalative",
            decision_policy={"tolerance_pct": 5.0, "min_samples_before_decision": 6},
               
        )

        if not ack or ack.get("status") != "OK":
            set_run_status(conn, run_id, "FAILED", note=f"Start Failed, ack: {ack}")
            LOGGER.error(f"[RUN SCHEDULER] Start run failed for run_id: {run_id}, ack: {ack}")
            #print("[START] Failed; updated runs.status: FAILED")
            return
        
        LOGGER.info(f"[RUN SCHEDULER] Start run succeded for run_id: {run_id}, status: RUNNING")
        set_run_status(conn, run_id, "RUNNING")

    except TimeoutError as to:
        LOGGER.error(f"[RUN SCHEDULER] timed out waiting for ack from pi. {to}")
        return
    
    print(f"[RUN] Running. run_id: {run_id}.\nWaiting {duration_in_seconds} seconds to accumulate sensor readings...")
    LOGGER.info(f"[RUN SCHEDULER] Run is now RUNNING for run_id: {run_id}. Waiting {duration_in_seconds} seconds to accumulate sensor readings...")
    
    #-------------wait for the duration of the slice (plus a buffer) and then send stop command------------------#
    try:
        time.sleep(duration_in_seconds + 5)
        ack2 = stop_run(zone, run_id)
        LOGGER.info(f"[RUN SCHEDULER]  Stop ACK received. {ack2}")
        #print("[STOP] ack: ", ack2)
        set_run_status(conn, run_id, "COMPLETED")
        LOGGER.info(f"[RUN SCHEDULER] Run completed successfully for run_id: {run_id}, status: COMPLETED")
    except KeyboardInterrupt:
        LOGGER.info("[RUN SCHEDULER] Keyboard Interrupt detected, Stopping the run...")
        #print("Keyboard Interrupt detected, manually stopping the run...")
        ack2 = stop_run(zone, run_id)
        #print("[STOP] ack: ", ack2)
        set_run_status(conn, run_id, "STOPPED", note="Stopped via Keyboard Interrupt")
        LOGGER.info(f"[RUN SCHEDULER] Run stopped via Keyboard Interrupt for run_id: {run_id}, status: STOPPED")
        #print("[RUN] Stopped and status updated: STOPPED")
    except Exception as e:
        LOGGER.error(f"[RUN SCHEDULER] Exception occurred: {e}, stopping the run...")
        #print(f"[RUN] Exception occurred: {e}, stopping the run...")
        ack2 = stop_run(zone, run_id)
        #print("[STOP] ack: ", ack2)
        set_run_status(conn, run_id, "FAILED", note=f"Exception occurred during run: {e}")
        LOGGER.info(f"[RUN SCHEDULER] Run Failed due to an exception for run_id: {run_id}, status: FAILED")
    finally:
        conn.close()

def simulated_run():
    crop, zone, sample_interval_s = get_hardcoded_crop_zone_sample_interval()

    #get connection to db
    conn = db_connect()

    #-------------get reference profile details: country, year, light profile for best country-year----------------#

    # get the country and year that achieved highest yield for crop
    try:
        #return the reference country and year for giving crop
        ref_country, ref_year = get_best_country_year_for_crop(conn, crop)
        LOGGER.info(f"[REF] crop: '{crop}': best country: {ref_country}, year achievied: {ref_year}")
    except Exception as e:
        LOGGER.error(f"[REF] Unable to retrieve country and year for '{crop}'")
        raise

    # retrieve the full year light profile for country, year and crop
    try:
        light_profile = get_best_light_profile(conn, crop, ref_country, ref_year)

        schedule_start_ts = light_profile[0][0]
        schedule_end_ts = light_profile[-1][0]
        
        count = len(light_profile)
        #LOGGER.info(f"[RUN SCHEDULER] Retrieved light profile successfully. count: {count}, first_ts: {schedule_start_ts}, last_ts={schedule_end_ts}")
    except Exception as e:
        #LOGGER.error(f"[RUN SCHEDULER] Unable to retrieve light profile: Ending Run...")
        raise Exception("[RUN SCHEDULER] Unable to retrieve light profile")
    
    print()
    LOGGER.info(f"[SCHEDULER] Retrieved light profile: count: {count}, first_ts: {schedule_start_ts}, last_ts={schedule_end_ts}")

    #-------------get schedule window from user input and slice the profile------------------#
    # #get date input from user
    try:
        start_ts, end_ts = parse_window_dates_input(ref_year)
    except Exception as e:
        raise
    
    print()
    LOGGER.info(f"[SCHEDULER] got start and end dates from user: start: {start_ts}, end: {end_ts}")

    try:
        slice_rows = slice_profile(light_profile, start_ts, end_ts)
        LOGGER.info(f"[RUN SCHEDULER] slice successful start: {start_ts} -> {end_ts}. rows: {len(slice_rows)}")
        duration_in_seconds = get_duration_of_slice(start_ts, end_ts)
        LOGGER.info(f"the duration of time slice in seconds: {duration_in_seconds}s")
        LOGGER.info(f"the duration of time slice with 5 seconds added on: seconds: {duration_in_seconds + 5}s")
    except ValueError as ve:
        LOGGER.error(f"[RUN SCHEDULER] slice was unsuccessful for start: {start_ts} -> {end_ts}")
        raise ve
    
    run_id = f"sim_{uuid.uuid4().hex[:8]}"
    
    try:
        create_run_with_metadata(conn, run_id, "CREATED", crop, ref_country, ref_year, start_ts, end_ts, sample_interval_s, zone, note=f"created by run scheduler")
        LOGGER.info(f"Run created successfully. run_id: {run_id}, crop: {crop}, country: {ref_country}, year: {ref_year}, total rows: {len(slice_rows)}")
    except Exception as e:
        LOGGER.error(f"Unable to create run: {e}")
    
    try:
        
        #build targets slots from sliced rows
        targets_slots = build_target_slots(slice_rows)
        if not targets_slots:
            raise RuntimeError("targets_slots is empty")
        
        if "slot" not in targets_slots[0]:
            raise KeyError(f"targets_slots[0] is missing 'slot'. key={list(targets_slots[0].keys())}")
        
        missing = [i for i, s in enumerate(targets_slots) if "slot" not in s]
        if missing:
            i = missing[0]
            raise KeyError(f"targets_slots[{i}] missing 'slot'. keys={list(targets_slots[i].keys())}")
        sim_rows = simulate_target_by_slot(targets_slots, CAPS_J_PER_HOUR)

        inserted = insert_actuator_tracking_sim(conn, run_id, targets_slots, sim_rows)
        print(f"[DB] inserted sim rows: {inserted}")

        LOGGER.info(f"[SIMULATION] simulated run started successfully")
        print("sim rows:", len(sim_rows))

        set_run_status(conn, run_id, "COMPLETED", note=f"simulated run completed successfully with {len(sim_rows)} simulated sensor readings")
        LOGGER.info(f"[SIMULATION] simulated run completed successfully with {len(sim_rows)} simulated sensor readings")

    except Exception as e:
        LOGGER.error(f"[RUN SCHEDULER] Failed to build target slots from sliced rows: {e}")
        raise e
    

# main flow
def main():
    if RUN_MODE == "REAL":
        real_run()
    elif RUN_MODE == "SIMULATION":
        LOGGER.info("Running in SIMULATION mode. simulated_run()")
        simulated_run()
    else:
        LOGGER.error(f"Invalid RUN_MODE: {RUN_MODE}. Must be 'REAL' or 'SIMULATION'.")
        raise ValueError(f"Invalid RUN_MODE: {RUN_MODE}. Must be 'REAL' or 'SIMULATION'.")
    
        

if __name__ == "__main__":
    try:
        main()
        LOGGER.info("Schedule Completed. Ending run...")
        SystemExit(0)
    except Exception as e:
        if RUN_MODE == "SIMULATION":
            LOGGER.error(f" FATAL error in run scheduler: {e}")
            set_run_status(db_connect(), run_id="unknown_run_id_due_to_exception", status="FAILED", note=f"FATAL error in run scheduler: {e}")
            SystemExit(0)
        else:
            LOGGER.error(f" FATAL: {e}")
            stop_run(zone="zone1", run_id = "unknown_run_id_due_to_exception")
            set_run_status(db_connect(), run_id="unknown_run_id_due_to_exception", status="FAILED", note=f"FATAL error in run scheduler: {e}")
            SystemExit(0)
