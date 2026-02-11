import os
from dotenv import load_dotenv

import json
import sys
from datetime import datetime
import time

import mysql.connector
from mysql.connector import Error
import paho.mqtt.client as mqtt_client
from pathlib import Path

import logging
from logging.handlers import RotatingFileHandler

from shared import spectral_conversion
from shared.spectral_conversion import bands_wm2_from_payload

# Load environment variables from .env file
load_dotenv()
# MQTT Configuration
MQTT_HOST = os.getenv('MQTT_HOST')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_TOPIC = os.getenv('MQTT_TOPIC')
MQTT_DECISION_TOPIC = os.getenv('MQTT_DECISION_TOPIC')
MQTT_USER = os.getenv('MQTT_USER')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')

# MySQL Configuration
MYSQL_HOST = os.getenv('MYSQL_HOST', '127.0.0.1')
MYSQL_PORT = int(os.getenv('MYSQL_PORT','3306'))
MYSQL_DB = os.getenv('MYSQL_DB','crop_lighting')
TABLE = os.getenv('TABLE','light_readings')
DECISION_TABLE = os.getenv('DECISION_TABLE','spectral_band_decisions')

MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

# SQL Insert Statement
INSERT_READINGS = f"""
INSERT INTO {TABLE}(
    ts, source, zone, run_id, run_seq,
    as7341_dev_id, bh1750_dev_id,
    as7341_gain, as7341_atime, as7341_astep, as7341_it_ms,
    bh1750_lux,
    as7341_415nm, as7341_445nm, as7341_480nm,
    as7341_515nm, as7341_555nm, as7341_590nm,
    as7341_630nm, as7341_680nm,
    as7341_clear, as7341_nir,
    blue_W_m2, green_W_m2, red_W_m2
) VALUES(
    %(ts)s, %(source)s, %(zone)s, %(run_id)s, %(run_seq)s,
    %(as7341_dev_id)s, %(bh1750_dev_id)s,
    %(as7341_gain)s, %(as7341_atime)s, %(as7341_astep)s, %(as7341_it_ms)s,
    %(bh1750_lux)s,
    %(as7341_415nm)s, %(as7341_445nm)s, %(as7341_480nm)s,
    %(as7341_515nm)s, %(as7341_555nm)s, %(as7341_590nm)s,
    %(as7341_630nm)s, %(as7341_680nm)s,
    %(as7341_clear)s, %(as7341_nir)s,
    %(blue_W_m2)s, %(green_W_m2)s, %(red_W_m2)s
    );
    """

INSERT_DECISIONS_SQL = f"""
INSERT INTO {DECISION_TABLE}(
    run_id, run_seq, ts_utc, source, zone, slot, ref_ts,
    tolerance_pct, min_sample_before_decision, notes,

    blue_measured_W_m2,
    blue_target_MJ_m2_hr,
    blue_target_j__m2_slot_total,
    blue_accumulated_j_m2_slot,
    blue_predicted_j_m2_slot,
    blue_decision,

    green_measured_W_m2,
    green_target_MJ_m2_hr,
    green_target_j__m2_slot_total,
    green_accumulated_j_m2_slot,
    green_predicted_j_m2_slot,
    green_decision,

    red_measured_W_m2,
    red_target_MJ_m2_hr,
    red_target_j__m2_slot_total,
    red_accumulated_j_m2_slot,
    red_predicted_j_m2_slot,
    red_decision
)
VALUES (
    %(run_id)s, %(run_seq)s, %(ts_utc)s, %(source)s, %(zone)s, %(slot)s, %(ref_ts)s,
    %(tolerance_pct)s, %(min_sample_before_decision)s, %(notes)s,

    %(blue_measured_W_m2)s,
    %(blue_target_MJ_m2_hr)s,
    %(blue_target_j__m2_slot_total)s,
    %(blue_accumulated_j_m2_slot)s,
    %(blue_predicted_j_m2_slot)s,
    %(blue_decision)s,

    %(green_measured_W_m2)s,
    %(green_target_MJ_m2_hr)s,
    %(green_target_j__m2_slot_total)s,
    %(green_accumulated_j_m2_slot)s,
    %(green_predicted_j_m2_slot)s,
    %(green_decision)s,

    %(red_measured_W_m2)s,
    %(red_target_MJ_m2_hr)s,
    %(red_target_j__m2_slot_total)s,
    %(red_accumulated_j_m2_slot)s,
    %(red_predicted_j_m2_slot)s,
    %(red_decision)s
)
ON DUPLICATE KEY UPDATE
    ts_utc = VALUES(ts_utc),
    ref_ts = VALUES(ref_ts),
    tolerance_pct = VALUES(tolerance_pct),
    min_sample_before_decision = VALUES(min_sample_before_decision),
    notes = VALUES(notes),

    blue_measured_W_m2 = VALUES(blue_measured_W_m2),
    blue_target_MJ_m2_hr = VALUES(blue_target_MJ_m2_hr),
    blue_target_j__m2_slot_total = VALUES(blue_target_j__m2_slot_total),
    blue_accumulated_j_m2_slot = VALUES(blue_accumulated_j_m2_slot),
    blue_predicted_j_m2_slot = VALUES(blue_predicted_j_m2_slot),
    blue_decision = VALUES(blue_decision),

    green_measured_W_m2 = VALUES(green_measured_W_m2),
    green_target_MJ_m2_hr = VALUES(green_target_MJ_m2_hr),
    green_target_j__m2_slot_total = VALUES(green_target_j__m2_slot_total),
    green_accumulated_j_m2_slot = VALUES(green_accumulated_j_m2_slot),
    green_predicted_j_m2_slot = VALUES(green_predicted_j_m2_slot),
    green_decision = VALUES(green_decision),

    red_measured_W_m2 = VALUES(red_measured_W_m2),
    red_target_MJ_m2_hr = VALUES(red_target_MJ_m2_hr),
    red_target_j__m2_slot_total = VALUES(red_target_j__m2_slot_total),
    red_accumulated_j_m2_slot = VALUES(red_accumulated_j_m2_slot),
    red_predicted_j_m2_slot = VALUES(red_predicted_j_m2_slot),
    red_decision = VALUES(red_decision);
"""

def setup_console_logger() -> logging.Logger:
    logger = logging.getLogger("backend_ingestion")
    if getattr(logger, "_configured_console", False):
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger._configured_console = True
    logger.info("Console logger initialized (file logging not yet enabled)")
    return logger

LOGGER = setup_console_logger()

def enable_file_logging(logger: logging.Logger)-> Path:
    repo_root = Path(__file__).resolve().parents[2]
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = (log_dir / "backend_ingestion.log").resolve()

    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == log_file:
            return log_file

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = RotatingFileHandler(
        log_file,
        maxBytes=2 *1024*1024,
        backupCount=5,
        encoding='utf-8',
        delay=True
    ) 
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("file logging enabled. log_file %s", log_file)
    return log_file


class SingleInstanceLock:
    def __init__(self, name: str):
        self.name = name
        self.handle = None

    def acquireLock(self):
        if os.name != 'nt':
            return
        
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutexW.restype = wintypes.HANDLE

        ctypes.set_last_error(0)

        self.handle = CreateMutexW(None, True, self.name)
        if not self.handle:
            LOGGER.error("[FILELOCK] Failed to create mutex.")
            raise RuntimeError("Failed to create mutex.")
        
        err = ctypes.get_last_error()
        ERROR_ALREADY_EXISTS = 183

        if err == ERROR_ALREADY_EXISTS:
            msg = f"[FILELOCK] Another instance is already running (mutex: {self.name})"
            LOGGER.error(msg)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            kernel32.CloseHandle(self.handle)
            self.handle = None
            raise RuntimeError(msg)
        
        LOGGER.info(f"[FILELOCK] acquired mutex lock: {self.name}")

    def releaseLock(self):
        if os.name != 'nt':
            return
        if not self.handle:
            return

        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            kernel32.ReleaseMutex(self.handle)
        except Exception:
            pass
        try:
            kernel32.CloseHandle(self.handle)
        except Exception:
            pass

        LOGGER.info(f"[FILELOCK] Released mutex lock: {self.name}")
        self.handle = None


if not MYSQL_PASSWORD:
    LOGGER.error("MYSQL_PASSWORD environment variable is not set.\nCreate a .env file (see .env.example)")
    raise RuntimeError("MYSQL_PASSWORD environment variable is not set.\nCreate a .env file (see .env.example)")

LOGGER.info(f"[CONFIG] Reference Irradiance E_ref = {spectral_conversion.E_REF_W_M2} W/m2")

def handle_decision_message(userdata, data: dict) -> None:
    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return
    
    run_seq = as_int_or_none(data.get("run_seq"))
    slot = as_int_or_none(data.get("slot"))
    source = data.get("source")
    zone = data.get("zone")

    ts_in = (data.get("ts") or "").strip()
    if ts_in.endswith("Z"):
        ts_in = ts_in.replace("Z", "+00:00")

    try:
        ts_dt = datetime.fromisoformat(ts_in)
        ts_utc = ts_dt.replace(tzinfo=None)
    except Exception as e:
        LOGGER.error(f"[DECISION] Invalid timestamp: {data.get('ts')!r} err: {e}")
        return
    
    ref_ts_in = data.get("ref_ts")
    ref_ts = None

    if ref_ts_in:
        try:
            s = str(ref_ts_in).strip()
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")

            ref_ts = datetime.fromisoformat(s).replace(tzinfo=None)
        except Exception:
            ref_ts = None

    policy = data.get("policy") or {}
    tolerance_pct = as_float_or_none(policy.get("tolerance_pct"))
    min_n = as_int_or_none(policy.get("min_samples_before_decision"))

    bands = data.get("bands") or {}
    blue = bands.get("blue") or {}
    green = bands.get("green") or {}
    red = bands.get("red") or {}

    row = {
        "run_id": run_id,
        "run_seq": run_seq,
        "ts_utc": ts_utc,
        "source": str(source) if source is not None else None,
        "zone": str(zone).strip().lower() if isinstance(zone, str) else zone,
        "slot": slot,
        "ref_ts": ref_ts,
        "tolerance_pct": tolerance_pct,
        "min_samples_before_decision": min_n,
        "notes": data.get("notes"),

        "blue_measured_W_m2": as_float_or_none(blue.get("measured_wm2")),
        "blue_target_MJ_m2_hr": as_float_or_none(blue.get("target_mj_m2_hr")),
        "blue_target_j_m2_slot_total": as_float_or_none(blue.get("target_j_m2_slot_total")),
        "blue_accumulated_j_m2_slot": as_float_or_none(blue.get("accumulated_j_m2_slot")),
        "blue_predicted_j_m2_slot": as_float_or_none(blue.get("predicted_j_m2_slot")),
        "blue_decision": blue.get("decision"),

        "green_measured_W_m2": as_float_or_none(green.get("measured_wm2")),
        "green_target_MJ_m2_hr": as_float_or_none(green.get("target_mj_m2_hr")),
        "green_target_j_m2_slot_total": as_float_or_none(green.get("target_j_m2_slot_total")),
        "green_accumulated_j_m2_slot": as_float_or_none(green.get("accumulated_j_m2_slot")),
        "green_predicted_j_m2_slot": as_float_or_none(green.get("predicted_j_m2_slot")),
        "green_decision": green.get("decision"),

        "red_measured_W_m2": as_float_or_none(red.get("measured_wm2")),
        "red_target_MJ_m2_hr": as_float_or_none(red.get("target_mj_m2_hr")),
        "red_target_j_m2_slot_total": as_float_or_none(red.get("target_j_m2_slot_total")),
        "red_accumulated_j_m2_slot": as_float_or_none(red.get("accumulated_j_m2_slot")),
        "red_predicted_j_m2_slot": as_float_or_none(red.get("predicted_j_m2_slot")),
        "red_decision": red.get("decision"),
    }
    
    if row["run_seq"] is None or row["slot"] is None:
        LOGGER.error(f"[DECISION] Missing run_seq or slot: run_seq: {row['run_seq']!r}, slot: {row['slot']!r}")
        return
    
    ok = db_insert_decision_with_reconnect(userdata, row)
    if ok:
        LOGGER.info(f"[DB] Decision inserted: run_id: {run_id}, run_seq: {run_seq}, slot: {slot}")

def as_float_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None
    
def as_int_or_none(value):
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None

#DB 
def db_connect():
    return mysql.connector.connect(
        host = MYSQL_HOST,
        port = MYSQL_PORT,
        user = MYSQL_USER,
        password = MYSQL_PASSWORD,
        database = MYSQL_DB,
        autocommit = True
    )

def db_connect_forever():
    backoff_s = 1
    while True:
        try:
            conn = db_connect()
            cur = conn.cursor()
            LOGGER.info("[DB] Connected to database")
            return conn, cur
        except Error as e:
            LOGGER.error(f"[DB] Connection to database failed: {e}. Retrying in {backoff_s} seconds...")
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, 60)

def db_insert_readings_with_reconnect(userdata, row):
    for attempt in (1,2):
        try:
            userdata["db_cursor"].execute(INSERT_READINGS, row)

            userdata["ok_inserts"] = userdata.get("ok_inserts", 0) + 1
            now = time.time()
            last = userdata.get("last_ok_log_ts", 0.0)

            if now - last >= 30:
                LOGGER.info(f"[DB] inserts ok in last window: {userdata['ok_inserts']} (last 30s)")
                userdata["ok_inserts"] = 0
                userdata["last_ok_log_ts"] = now
            
            return True
        
        except Error as e:            
            errno = getattr(e, "errno", None)
            if errno == 1452:
                LOGGER.error(f"[DB] Foreign key constraint failed. Check that 'source', 'zone', and 'run_id' exist.")
                return False
            
            LOGGER.error(f"[DB] Insert failed: {e}. Attempt {attempt}/2...")

            #try reconnecting
            if attempt == 1:
                try:
                    try:
                        userdata["db_cursor"].close()
                    except Exception:
                        pass
                    try:
                        userdata["db_conn"].close()
                    except Exception:
                        pass

                    conn, cur  = db_connect_forever()
                    userdata["db_conn"] = conn
                    userdata["db_cursor"] = cur

                    LOGGER.info("[DB] Reconnected after insert failure. Retrying insert once...")
                    continue
                except Exception as reconnect_err:
                    LOGGER.error(f"[DB] Reconnection failed: {reconnect_err}.")
                    return False
            return False

def db_insert_decision_with_reconnect(userdata, row):
    for attempt in (1,2):
        try:
            userdata["db_cursor"].execute(INSERT_DECISIONS_SQL, row)
            userdata["db_conn"].commit()
            return True
        
        except Error as e:
            errno = getattr(e, "errno", None)
            if errno == 1452:
                LOGGER.error(f"[DB] Foreign key constraint failed on decision insert. Check that 'source', 'zone', and 'run_id' exist.")
                return False
            
            LOGGER.error(f"[DB] Decision insert failed: {e}. Attempt {attempt}/2...")

            #try reconnecting
            if attempt == 1:
                try:
                    try:
                        userdata["db_cursor"].close()
                    except Exception:
                        pass
                    try:
                        userdata["db_conn"].close()
                    except Exception:
                        pass

                    conn, cur  = db_connect_forever()
                    userdata["db_conn"] = conn
                    userdata["db_cursor"] = cur

                    LOGGER.info("[DB] Reconnected after decision insert failure. Retrying insert once...")
                    continue
                except Exception as reconnect_err:
                    LOGGER.error(f"[DB] Reconnection failed: {reconnect_err}.")
                    return False
            return False

# MQTT Callbacks
def on_connect(client, userdata, flags, reason_code, properties=None):
    session_present = None
    try:
        session_present = flags.get("session present")
    except Exception:
        session_present = None

    LOGGER.info(f"[MQTT] Connected reason_code: {reason_code}, session_present: {session_present}")
    client.subscribe(MQTT_TOPIC, qos=1)
    client.subscribe(MQTT_DECISION_TOPIC, qos=1)
    LOGGER.info(f"[MQTT] Subscribed to topics: {MQTT_TOPIC}, {MQTT_DECISION_TOPIC}")

def on_message(client, userdata, msg):
    raw = msg.payload.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw)
        msg_type = data.get("type")
        if msg_type == "DECISION":
            handle_decision_message(userdata, data)
            return
    except json.JSONDecodeError as e:
        LOGGER.error(f"[MQTT] invalid JSON on topic {msg.topic}:\n error: {e}\n payload = {raw!r}")
        #print(f"[mqtt] invalid JSON on topic {msg.topic}: {e} | payload = {raw!r}")
        return
    
    # required fields
    ts_in = data.get('ts')
    source = data.get('source')
    zone = data.get('zone')
    if isinstance(zone, str):
        zone = zone.strip().lower()
        data['zone'] = zone
    if not ts_in or source is None or not zone:
        LOGGER.error(f"[MQTT] Missing required fields in payload: timestamp = {ts_in!r}, source = {source!r}, zone = {zone!r}")
    #   print(f"[MQTT] Missing required fields in payload: ts = {ts_in!r}, source = {source!r}, zone = {zone!r}" )
        return

    #convert iso timestamp to mysql datetime format
    #ts_mysql = datetime.fromisoformat(ts_in.strip("Z").replace("T", " "))
    try:
        ts_in = (ts_in or "").strip()
        if ts_in.endswith("Z"):
            ts_in = ts_in.replace("Z", "+00:00")

        ts_dt = datetime.fromisoformat(ts_in) #aware datetime if offset present
        ts_mysql = ts_dt.replace(tzinfo=None) #store as naive UTC for MySQL DATETIME
    except Exception as e:
        LOGGER.error(f"[MQTT] Invalid timestamp format in payload: ts: {ts_in!r} error: {e}")
       #print(f"[MQTT] invalid ts format: ts={ts_in!r} error={e}")
        return

    if ts_mysql is None or source is None or zone is None:
        LOGGER.error(f"[MQTT] Missing required fields in payload: ts = {ts_in!r}, source = {source!r}, zone = {zone!r}")
        #print(f"[mqtt] missing required fields in payload: ts= {ts_in!r}, source= {source!r}, zone= {zone!r}")
        return
    
    #calculate irradiance values
    bands = bands_wm2_from_payload(data)

    run_id = data.get("run_id")
    if run_id is None:
        return
    run_id = str(run_id).strip()
    if run_id == "" or run_id.lower() == "none":
        return
    
    row = {
        "ts": ts_mysql,
        "source": str(source),
        "zone": str(zone),
        "run_id": run_id,
        "run_seq": as_int_or_none(data.get("run_seq")),
        "as7341_dev_id": data.get("as7341_dev_id"),
        "bh1750_dev_id": data.get("bh1750_dev_id"),

        "as7341_gain": as_float_or_none(data.get("as7341_gain")),
        "as7341_atime":as_int_or_none(data.get("as7341_atime")),
        "as7341_astep": as_int_or_none(data.get("as7341_astep")),
        "as7341_it_ms": as_float_or_none(data.get("as7341_it_ms")),
        
        "bh1750_lux": as_float_or_none(data.get("bh1750_lux")),

        "as7341_415nm": as_int_or_none(data.get("as7341_415nm")),
        "as7341_445nm": as_int_or_none(data.get("as7341_445nm")),
        "as7341_480nm": as_int_or_none(data.get("as7341_480nm")),
        "as7341_515nm": as_int_or_none(data.get("as7341_515nm")),
        "as7341_555nm": as_int_or_none(data.get("as7341_555nm")),
        "as7341_590nm": as_int_or_none(data.get("as7341_590nm")),
        "as7341_630nm": as_int_or_none(data.get("as7341_630nm")),
        "as7341_680nm": as_int_or_none(data.get("as7341_680nm")),
        "as7341_clear": as_int_or_none(data.get("as7341_clear")),
        "as7341_nir": as_int_or_none(data.get("as7341_nir")),

        "blue_W_m2": bands.get("blue_W_m2"),
        "green_W_m2": bands.get("green_W_m2"),
        "red_W_m2": bands.get("red_W_m2"),

    }

    try:
       ok = db_insert_readings_with_reconnect(userdata, row)
       if not ok:
            return
    except Error as e:
        LOGGER.error(f"[DB] Insert failed: {e}\nrow: {row}")

def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    LOGGER.info(f"[MQTT] Disconnected with reason_code: {reason_code}, flags: {disconnect_flags}.")
    #print(f"[MQTT] Disconnected with reason_code: {reason_code}.")

def run_mqtt_forever(conn, cur):
    if not (MQTT_USER and MQTT_PASSWORD):
        LOGGER.error("MQTT_USER / MQTT_PASSWORD missing in backend .env")
        raise RuntimeError("MQTT_USER / MQTT_PASSWORD missing in backend .env")
    if not MQTT_HOST:
        LOGGER.error("MQTT_HOST is not set (expected Pi Netbird IP address.)")
        raise RuntimeError("MQTT_HOST is not set (expected Pi Netbird IP address.)")
    if not MQTT_TOPIC:
        LOGGER.error("MQTT_TOPIC is not set.")
        raise RuntimeError("MQTT_TOPIC is not set.")
    backoff_s = 1

    while True:
        client = mqtt_client.Client(
            client_id = "backend_sensor_data_process",
            clean_session = False,            
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
        )

        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        client.user_data_set({
            'db_conn': conn,
            'db_cursor': cur,
            'ok_inserts': 0,
            'last_ok_log_ts': time.time(),
            })
        client.reconnect_delay_set(min_delay=1, max_delay=60)

        try:
            LOGGER.info(f"[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT}...")
            #print(f"[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT}...")
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

            backoff_s = 1
            LOGGER.info("[RUN] Starting mqtt client loop... ctrl+C to stop")
            #print("[RUN] Starting mqtt client loop... ctrl+C to stop")
            client.loop_forever()
            LOGGER.info("[MQTT] MQTT client loop has exited unexpectedly. reconnection in 5 seconds...")
            #print(f"\n[MQTT] MQTT client loop has exited unexpectedly. reconnection in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            LOGGER.info("\n[RUN] stopping MQTT client loop (keyboard interrupt) ...")
            #print("\n[RUN] stopping mqtt client loop (keyboard interrupt) ...")
            try:
                client.disconnect()
            except Exception:
                pass
            break
        except Exception as e:
            LOGGER.error(f"[MQTT] MQTT connection/loop error: {e!r}. Reconnecting in {backoff_s} seconds...")
            #print(f"[MQTT] MQTT connection/loop error: {e!r}. Reconnecting in {backoff_s} seconds...")
            try:
                client.disconnect()
            except Exception:
                pass
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, 60)
            continue



def main():
    
    conn = None
    cur = None
        
    try:
        conn, cur = db_connect_forever()
        run_mqtt_forever(conn, cur)
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass

if __name__ == "__main__":
    lock = SingleInstanceLock(r"Local\HonoursProject_BackendIngestion")
    try:
        lock.acquireLock()
        enable_file_logging(LOGGER)
        LOGGER.info("[LOGGER] pid=%s, ppid=%s, argv=%s", os.getpid(), os.getppid(), sys.argv)
        LOGGER.info("[FILELOCK] CONFIRMED SINGLETON (MUTEX HELD)")
        main()
    except RuntimeError as e:
        msg = str(e)
        LOGGER.error(f"[FILELOCK] Runtime Error: {str(e)}")

        if "Another instance is already running" in msg:
            raise SystemExit(2)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception:
        LOGGER.exception("fatal error")
        raise SystemExit(1)
    finally:
        lock.releaseLock()