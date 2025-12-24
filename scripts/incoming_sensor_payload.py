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
    ts, source, zone,
    as7341_dev_id, bh1750_dev_id,
    bh1750_lux,
    as7341_415nm, as7341_445nm, as7341_480nm,
    as7341_515nm, as7341_555nm, as7341_590nm,
    as7341_630nm, as7341_680nm,
    as7341_clear, as7341_nir
) VALUES(
    %(ts)s, %(source)s, %(zone)s,
    %(as7341_dev_id)s, %(bh1750_dev_id)s,
    %(bh1750_lux)s,
    %(as7341_415nm)s, %(as7341_445nm)s, %(as7341_480nm)s,
    %(as7341_515nm)s, %(as7341_555nm)s, %(as7341_590nm)s,
    %(as7341_630nm)s, %(as7341_680nm)s,
    %(as7341_clear)s, %(as7341_nir)s
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

    #convert iso timestamp to mysql datetime format
    ts_mysql = datetime.fromisoformat(ts_in.strip("Z").replace("T", " "))

    if ts_mysql is None or source is None or zone is None:
        print(f"[mqtt] missing required fields in payload: ts= {ts_in!r}, source= {source!r}, zone= {zone!r}")
        return
    
    row = {
        "ts": ts_mysql,
        "source": str(source),
        "zone": str(zone),

        "as7341_dev_id": data.get("as7341_dev_id"),
        "bh1750_dev_id": data.get("bh1750_dev_id"),

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