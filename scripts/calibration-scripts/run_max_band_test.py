import os
import sys
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

from dotenv import load_dotenv
import mysql.connector
import paho.mqtt.client as mqtt

# ------------------------------------------------------------
# Make project root importable + load .env from project root
# ------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from shared.spectral_conversion import bands_wm2_from_payload  # noqa


# ============================================================
# HARD CODED CONFIG (EDIT THESE)
# ============================================================
ZONE = "zone1"
LED_COLOUR = "BLUE ONLY"   # "BLUE ONLY" | "GREEN ONLY" | "RED ONLY"
NOTES = "desk LED strip, max brightness, ~3-5 inches from sensor"
DEFAULT_SAMPLE_INTERVAL_S = 1.0


# ============================================================
# MQTT CONFIG (.env)
# ============================================================
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC")

MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")


# ============================================================
# MYSQL CONFIG (.env)
# ============================================================
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DB = os.getenv("MYSQL_DB", "crop_lighting")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")


# ============================================================
# TOPICS
# ============================================================
def get_topics(zone: str) -> tuple[str, str]:
    cmd_topic = f"adunn/control/{zone}/cmd"
    ack_topic = f"adunn/control/{zone}/ack"
    return cmd_topic, ack_topic


# ============================================================
# SQL (matches your table exactly)
# ============================================================
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


def parse_ts_mysql_utc(ts_str: str):
    # matches your ingestion convention: store naive UTC DATETIME
    try:
        ts_str = (ts_str or "").strip()
        if not ts_str:
            return None
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")

        ts_dt = datetime.fromisoformat(ts_str)   # aware if offset present
        return ts_dt.replace(tzinfo=None)        # naive UTC
    except Exception:
        return None


def f0(x) -> float:
    # table columns are NOT NULL, so force numbers
    try:
        return 0.0 if x is None else float(x)
    except Exception:
        return 0.0


# ============================================================
# CONTROL CLIENT (START/STOP + wait for ACK)
# ============================================================
def make_mqtt_client(client_id: str) -> mqtt.Client:
    c = mqtt.Client(client_id=client_id)
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    return c


def publish_and_wait_ack(
    client: mqtt.Client,
    cmd_topic: str,
    ack_topic: str,
    payload: dict,
    expect_type: str,
    timeout_s: int = 10,
) -> dict:
    """
    Very simple ACK wait:
    - Subscribed to ack_topic already
    - Publish command
    - Block until an ACK arrives that matches expect_type (+ run_id + zone)
    Returns the ack dict or raises RuntimeError.
    """
    ack_event = Event()
    ack_box = {"ack": None}

    run_id = str(payload.get("run_id") or "").strip()
    zone = str(payload.get("zone") or "").strip().lower()

    def on_ack(_client, _userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except Exception:
            return

        # Common patterns:
        # - ack might be {"type":"ACK","cmd":"START",...}
        # - or {"type":"START_ACK",...}
        # We'll accept a few variants, but keep it strict on run_id + zone.
        d_type = (d.get("type") or "").upper()
        d_cmd = (d.get("cmd") or "").upper()

        if str(d.get("run_id") or "").strip() != run_id:
            return
        if str(d.get("zone") or "").strip().lower() != zone:
            return

        # Decide if it matches expected
        if d_type == "ACK" and d_cmd == expect_type:
            ack_box["ack"] = d
            ack_event.set()
            return

        if d_type in {f"{expect_type}_ACK", f"ACK_{expect_type}"}:
            ack_box["ack"] = d
            ack_event.set()
            return

    # temporarily hook ack handler
    prev_on_message = client.on_message
    def router(_client, _userdata, msg):
        if msg.topic == ack_topic:
            on_ack(_client, _userdata, msg)
        if prev_on_message:
            prev_on_message(_client, _userdata, msg)

    client.on_message = router

    # publish
    print(f"[MQTT] Publishing {expect_type} -> {cmd_topic}")
    client.publish(cmd_topic, json.dumps(payload), qos=1)

    ok = ack_event.wait(timeout_s)
    if not ok:
        raise RuntimeError(f"No ACK received for {expect_type} within {timeout_s}s (topic={ack_topic})")

    return ack_box["ack"]


# ============================================================
# MAIN
# ============================================================
def main():
    if LED_COLOUR not in {"BLUE ONLY", "GREEN ONLY", "RED ONLY"}:
        raise ValueError(f'LED_COLOUR must be "BLUE ONLY"/"GREEN ONLY"/"RED ONLY". Got {LED_COLOUR!r}')

    if not MQTT_HOST or not MQTT_TOPIC:
        raise RuntimeError("MQTT_HOST and MQTT_TOPIC must be set in .env")

    cmd_topic, ack_topic = get_topics(ZONE)

    # one run_id used for START/STOP handshake (pi side often expects this)
    run_id = f"max_band_cal_{uuid.uuid4().hex[:10]}"
    cal_run_id = uuid.uuid4().hex  # your DB calibration run id (independent)
    print(f"[CAL] cal_run_id={cal_run_id}")
    print(f"[CAL] run_id(for START/STOP)={run_id}")
    print(f"[CAL] led_colour={LED_COLOUR}")

    # -----------------------------
    # CONTROL CLIENT: START sensors and wait for ACK
    # -----------------------------
    ctl = make_mqtt_client(client_id=f"cal_ctl_{uuid.uuid4().hex[:8]}")
    ctl.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    ctl.subscribe(ack_topic, qos=1)
    ctl.loop_start()

    # start_payload = {
    #     "type": "START",
    #     "run_id": run_id,
    #     "ts": datetime.utcnow().isoformat() + "Z",
    #     "source": "calibration_script",
    #     "zone": ZONE,
    # }
    start_payload = {
        "type": "START",
        "run_id": run_id,
        "sample_interval_s": 5,
        "run_start_ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),

        # #time anchors
        # "run_start_ts": run_start_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        # "ref_start_ts": ref_start_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),

        # #metadata
        # "ref_meta": ref_meta, # e.g. {"crop": "tomatoes", "country": "Belgium", "year": 2020}

        # #comparison details
        # "compare_mode": compare_mode, # cumulative
        # "decision_policy": decision_policy,

        # #target sequence for the run
        # #each element corresponds to slot index = floor(elapsed_s/3600)
        # "targets_by_slot": targets_by_slot
    }


    ack = publish_and_wait_ack(
        ctl, cmd_topic, ack_topic, start_payload, expect_type="START", timeout_s=10
    )
    print(f"[MQTT] START ACK: {ack}")

    # -----------------------------
    # DB connect
    # -----------------------------
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        autocommit=True,
    )
    cur = conn.cursor()

    # -----------------------------
    # DATA CLIENT: subscribe and capture 1-hour window
    # -----------------------------
    cal_run_seq = 0
    blue_j = green_j = red_j = 0.0
    last_ts = None
    slot_start = None
    slot_end = None

    def on_data_connect(client, userdata, flags, rc, properties=None):
        if rc != 0:
            print(f"[MQTT] data connect failed rc={rc}")
            sys.exit(1)
        print(f"[MQTT] Data connected. Subscribing: {MQTT_TOPIC}")
        client.subscribe(MQTT_TOPIC, qos=0)
        print("[CAL] Waiting for first sensor sample to set the hour window...")

    def on_data_message(client, userdata, msg):
        nonlocal cal_run_seq, blue_j, green_j, red_j, last_ts, slot_start, slot_end

        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # ignore decisions; calibration is sensor-only
        if data.get("type") == "DECISION":
            return

        ts_in = data.get("ts")
        source = data.get("source")
        zone = data.get("zone")
        if isinstance(zone, str):
            zone = zone.strip().lower()

        if not ts_in or source is None or not zone:
            return

        ts_mysql = parse_ts_mysql_utc(ts_in)
        if ts_mysql is None:
            return

        # init hour window from first valid sample
        if slot_start is None:
            slot_start = ts_mysql.replace(minute=0, second=0, microsecond=0)
            slot_end = slot_start + timedelta(hours=1)
            print(f"[CAL] Window: {slot_start} -> {slot_end}")
            print("[CAL] Leave the LED on for the full hour window.")

        # stop at hour boundary
        if ts_mysql >= slot_end:
            print("[CAL] Hour complete. Disconnecting data client...")
            client.disconnect()
            return

        bands = bands_wm2_from_payload(data)
        blue_w = bands.get("blue_W_m2")
        green_w = bands.get("green_W_m2")
        red_w = bands.get("red_W_m2")

        # dt seconds
        if last_ts is None:
            dt_s = DEFAULT_SAMPLE_INTERVAL_S
        else:
            dt = (ts_mysql - last_ts).total_seconds()
            dt_s = dt if 0.0 < dt < 60.0 else DEFAULT_SAMPLE_INTERVAL_S

        blue_j += f0(blue_w) * dt_s
        green_j += f0(green_w) * dt_s
        red_j += f0(red_w) * dt_s

        remaining_s = (slot_end - ts_mysql).total_seconds()
        blue_pred = blue_j + (f0(blue_w) * remaining_s)
        green_pred = green_j + (f0(green_w) * remaining_s)
        red_pred = red_j + (f0(red_w) * remaining_s)

        cur.execute(
            INSERT_SQL,
            (
                cal_run_id, cal_run_seq, ts_mysql,
                LED_COLOUR, str(zone), str(source),
                slot_start, slot_end,
                float(dt_s), NOTES,

                f0(blue_w), float(blue_j), float(blue_pred),
                f0(green_w), float(green_j), float(green_pred),
                f0(red_w), float(red_j), float(red_pred),
            ),
        )

        if cal_run_seq % 30 == 0:
            print(f"[CAL] seq={cal_run_seq} blue_J={blue_j:.2f} green_J={green_j:.2f} red_J={red_j:.2f}")

        cal_run_seq += 1
        last_ts = ts_mysql

    data_client = make_mqtt_client(client_id=f"cal_data_{uuid.uuid4().hex[:8]}")
    data_client.on_connect = on_data_connect
    data_client.on_message = on_data_message
    data_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    data_client.loop_forever()

    # -----------------------------
    # FINAL OUTPUT
    # -----------------------------
    print("\n============ FINAL TOTALS (J/m² per hour) ============")
    print(f"cal_run_id : {cal_run_id}")
    print(f"led_colour : {LED_COLOUR}")
    print(f"BLUE  J/m² : {blue_j:.2f}")
    print(f"GREEN J/m² : {green_j:.2f}")
    print(f"RED   J/m² : {red_j:.2f}")

    cur.close()
    conn.close()

    # -----------------------------
    # CONTROL CLIENT: STOP sensors and wait for ACK
    # -----------------------------
    stop_payload = {
        "type": "STOP",
        "run_id": run_id,
        "ts": datetime.utcnow().isoformat() + "Z",
        "source": "calibration_script",
        "zone": ZONE,
    }
    ack2 = publish_and_wait_ack(
        ctl, cmd_topic, ack_topic, stop_payload, expect_type="STOP", timeout_s=10
    )
    print(f"[MQTT] STOP ACK: {ack2}")

    ctl.loop_stop()
    ctl.disconnect()


if __name__ == "__main__":
    main()