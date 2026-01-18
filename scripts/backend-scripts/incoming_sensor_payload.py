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

# Load environment variables from .env file
load_dotenv()
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

MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

# SQL Insert Statement
INSERT_SQL = f"""
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
# DATASHEET REFERENCE IRRADIANCE: Ee = (107.67µW/cm² / 100)= 1.0767 W/m²
E_REF_W_M2 = 107.67 / 100 # 1.0767

# DATASHEET REFERENCE COUNTS again=64, atime = 2.78ms (2700k warm white LED)
GAIN_REF = 64.0
IT_REF_MS = 27.8
C_REF = {
    415: 55,
    445: 110,
    480: 210,
    515: 390,
    555: 590,
    590: 840,
    630: 1350,
    680: 1070
}

def setup_logger() -> logging.Logger:
    repo_root = Path(__file__).resolve().parents[2]
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "backend_ingestion.log"

    logger = logging.getLogger("backend_ingestion")
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
LOGGER = setup_logger()

if not MYSQL_PASSWORD:
    LOGGER.error("MYSQL_PASSWORD environment variable is not set.\nCreate a .env file (see .env.example)")
    raise RuntimeError("MYSQL_PASSWORD environment variable is not set.\nCreate a .env file (see .env.example)")

LOGGER.info(f"[CONFIG] Reference Irradiance E_ref = {E_REF_W_M2} W/m2")

class SingleInstanceLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fp = None

    def acquireLock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fp = open(self.lock_path, 'a+', encoding='utf-8')

        self.fp.seek(0)

        try:
            if os.name == 'nt':
                import msvcrt
                # lock 1 byte (non-blocking)
                msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
                LOGGER.info(f"Acquired lock on {self.lock_path}")
            else:
                import fcntl
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            LOGGER.error(f"Failed to acquire lock on {self.lock_path}\nAnother instance is already running.")
            raise RuntimeError(f"Another instance is already running (lock: {self.lock_path})")
        
        try:
            self.fp.seek(0)
            self.fp.truncate()
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            LOGGER.info(f"Wrote PID {os.getpid()} to lock file {self.lock_path}")
        except Exception:
            pass

    def releaseLock(self):
        if not self.fp:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                self.fp.seek(0)
                msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
                LOGGER.info(f"Released lock on {self.lock_path}")
            else:
                import fcntl
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
                LOGGER.info(f"Released lock on self.lock_path")
        except Exception:
            pass
        try:
            self.fp.close()
        except Exception:
            pass
        self.fp = None

def wm2_from_counts_ref(counts:int | None, wl_nm: int, gain_meas: float| None, it_meas_ms: float | None):
    """
    Convert as7341 counts to an irradiance proxy (W/m2) using datasheet reference counts.
    applies normilization for gain and integration time so the result stays comparable if settings change.
    """
    if counts is None:
        return None
    
    c_ref = C_REF.get(wl_nm)
    if not c_ref:
        return None
    
    # if metadata is missing, fall back to assume reference settings
    if not gain_meas or gain_meas <= 0:
        gain_meas = GAIN_REF
    if not it_meas_ms or it_meas_ms <=0:
        it_meas_ms = IT_REF_MS

    norm = (GAIN_REF / float(gain_meas)) * (IT_REF_MS / float(it_meas_ms))
    return float(counts) * (E_REF_W_M2 / float(c_ref)) * norm

def safe_sum(*xs):
    vals = [v for v in xs if v is not None]
    return sum(vals) if vals else None

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

def db_insert_with_reconnect(userdata, row):
    for attempt in (1,2):
        try:
            userdata["db_cursor"].execute(INSERT_SQL, row)
            return True
        
        except Error as e:            
            errno = getattr(e, "errno", None)
            if errno == 1452:
                LOGGER.error(f"[DB] Foreign key constraint failed. Check that 'source', 'zone', and 'run_id' exist.")
                return False
            
            LOGGER.error("[DB] Insert failed: {e}. Attempt {attempt}/2...")

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

# MQTT Callbacks
def on_connect(client, userdata, flags, reason_code, properties=None):
    session_present = None
    try:
        session_present = flags.get("session present")
    except Exception:
        session_present = None

    LOGGER.info(f"[MQTT] Connected reason_code: {reason_code}, session_present: {session_present}")
    client.subscribe(MQTT_TOPIC, qos=1)
    LOGGER.info(f"[MQTT] Subscribed to topic: {MQTT_TOPIC}")

def on_message(client, userdata, msg):
    raw = msg.payload.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        LOGGER.error(f"[MQTT] invalid JSON on topic {msg.MQTT_TOPIC}:\n error: {e}\n payload = {raw!r}")
        #print(f"[mqtt] invalid JSON on topic {msg.topic}: {e} | payload = {raw!r}")
        return
    
    # required fields
    ts_in = data.get('ts')
    source = data.get('source')
    zone = data.get('zone')

    if not ts_in or source is None or zone is None:
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
    
    gain_meas = as_float_or_none(data.get("as7341_gain"))
    it_meas_ms = as_float_or_none(data.get("as7341_it_ms"))
    
    #calculate irradiance values
    w415 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_415nm")), 415, gain_meas, it_meas_ms)
    w445 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_445nm")), 445, gain_meas, it_meas_ms)
    w480 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_480nm")), 480, gain_meas, it_meas_ms)

    w515 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_515nm")), 515,  gain_meas, it_meas_ms)
    w555 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_555nm")), 555,  gain_meas, it_meas_ms)
    w590 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_590nm")), 590,  gain_meas, it_meas_ms)

    w630 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_630nm")), 630,  gain_meas, it_meas_ms)
    w680 = wm2_from_counts_ref(as_int_or_none(data.get("as7341_680nm")), 680,  gain_meas, it_meas_ms)

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

        "blue_W_m2": safe_sum(w415, w445, w480),
        "green_W_m2": safe_sum(w515, w555, w590),
        "red_W_m2": safe_sum(w630, w680),

    }

    try:
        cur = userdata['db_cursor']
        cur.execute(INSERT_SQL, row)
        LOGGER.info(f"[MQTT] Inserted topic {msg.MQTT_TOPIC}, timestamp: {row['ts']}, source: {row['source']}, zone: {row['zone']}")
        #print(f"[mqtt] inserted topic {msg.topic}, timestamp: {row['ts']}, source: {row['source']}, zone: {row['zone']}")
    except Error as e:
        LOGGER.error(f"[MQTT] Insert failed: {e}\nrow: {row}")
        #print(f"[mqtt] insert failed: {e} | row= {row}")

def on_disconnect(client, userdata, reason_code, properties=None):
    LOGGER.info(f"[MQTT] Disconnected with reason_code: {reason_code}.")
    #print(f"[MQTT] Disconnected with reason_code: {reason_code}.")

def run_mqtt_forever(cur):
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

        client.user_data_set({'db_cursor': cur})
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
    try:
        conn = db_connect()
        cur = conn.cursor()
        LOGGER.info("[DB] Connected to database")
        #print("[db] connected to database")
    except Error as e:
        LOGGER.error(f"[DB] Connection  to database failed: {e}")
        #print(f"[db] connectionn failed: {e}")
        sys.exit(1)
    
    try:
        run_mqtt_forever(cur)
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    LOCK_FILE = Path(__file__).resolve().parents[2] / "logs" / "incoming_sensor_payload.lock"

    lock = SingleInstanceLock(LOCK_FILE)
    try:
        lock.acquireLock()
        LOGGER.info(f"[FILELOCK] Acquired lock: {LOCK_FILE}")
    except RuntimeError as e:
        LOGGER.error(f"[FILELOCK] Runtime Error: {str(e)}")
        #print(f"[FILELOCK] RuntimeError: {str(e)}")
        raise SystemExit(2)
    
    try:
        main()
    finally:
        lock.releaseLock()
        LOGGER.info(f"[FILELOCK] Release lock: {LOCK_FILE}")
    