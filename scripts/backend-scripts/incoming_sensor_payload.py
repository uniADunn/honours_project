import os
from dotenv import load_dotenv

import json
import sys
from datetime import datetime

import mysql.connector
from mysql.connector import Error
import paho.mqtt.client as mqtt_client

# Load environment variables from .env file
load_dotenv()

# DATASHEET REFERENCE IRRADIANCE: Ee = (107.67µW/cm² / 100)= 1.0767 W/m²
E_REF_W_M2 = 107.67 / 100 # 1.0767
print(f"[config] reference irradiance E_ref = {E_REF_W_M2} W/m2")
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

# MQTT Configuration
MQTT_HOST = os.getenv('MQTT_HOST', '127.0.0.1')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_TOPIC = os.getenv('MQTT_TOPIC')

# MySQL Configuration
MYSQL_HOST = os.getenv('MYSQL_HOST', '127.0.0.1')
MYSQL_PORT = int(os.getenv('MYSQL_PORT','3306'))
MYSQL_DB = os.getenv('MYSQL_DB','crop_lighting')

MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

TABLE = os.getenv('TABLE','light_readings')

if not MYSQL_PASSWORD:
    raise RuntimeError("MYSQL_PASSWORD environment variable is not set.\nCreate a .env file (see .env.example)")

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

# MQTT Callbacks
def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[mqtt] connected with reason_code= {reason_code}")
    client.subscribe(MQTT_TOPIC)
    print(f"[mqtt] subscribed to topic: {MQTT_TOPIC}")

def on_message(client, userdata, msg):
    raw = msg.payload.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[mqtt] invalid JSON on topic {msg.topic}: {e} | payload = {raw!r}")
        return
    
    # required fields
    ts_in = data.get('ts')
    source = data.get('source')
    zone = data.get('zone')

    if not ts_in or source is None or zone is None:
        print(f"[MQTT] Missing required fields in payload: ts = {ts_in!r}, source = {source!r}, zone = {zone!r}" )
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
        print(f"[MQTT] invalid ts format: ts={ts_in!r} error={e}")
        return

    if ts_mysql is None or source is None or zone is None:
        print(f"[mqtt] missing required fields in payload: ts= {ts_in!r}, source= {source!r}, zone= {zone!r}")
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
        print(f"[mqtt] inserted topic {msg.topic}, timestamp: {row['ts']}, source: {row['source']}, zone: {row['zone']}")
    except Error as e:
        print(f"[mqtt] insert failed: {e} | row= {row}")

def main():
    try:
        conn = db_connect()
        cur = conn.cursor()
        print("[db] connected to database")
    except Error as e:
        print(f"[db] connectionn failed: {e}")
        sys.exit(1)
    
    client = mqtt_client.Client(client_id="backend_sensor_data_process")
    client.on_connect = on_connect
    client.on_message = on_message

    #pass db cursor via userdata
    client.user_data_set({'db_cursor': cur})

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    except Exception as e:
        print(f"[mqtt] connection failed: {e}")
        sys.exit(1)

    print("[run] starting mqtt client loop... ctrl+c to stop")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[run] stopping mqtt client loop ...")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()