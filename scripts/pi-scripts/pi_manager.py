import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import board
import busio
import adafruit_bh1750
import adafruit_as7341

from collections import deque

import os
from dotenv import load_dotenv

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

load_dotenv()

def setup_file_logger(
        logger_name: str = "pi_manager_zone1",
        filename: str = "pi_manager_zone1.log",
        level: int = logging.INFO)-> logging.Logger:
    logger = logging.getLogger(logger_name)

    if getattr(logger, "_configured_file", False):
        return logger
    
    logger.setLevel(level)
    logger.propagate = False

    repo_root = Path(__file__).resolve().parents[2]
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / filename).resolve()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(messages)s")

    fh = RotatingFileHandler(
        log_file,
        maxBytes=2*1024*1024,
        backupCount=5,
        encoding="utf-8",
        delay=True
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger._configured_file = True
    logger.info(f"[PI MANAGER] Pi Manager Logging Initialized. log_file: {log_file}")
    return logger
LOGGER = setup_file_logger()

#MQTT CONFIGURATION
BROKER_HOST = os.getenv("MQTT_HOST")
BROKER_PORT = int(os.getenv("MQTT_PORT"))
BROKER_USER = os.getenv("MQTT_USER")
BROKER_PASSWORD = os.getenv("MQTT_PASSWORD")

DATA_TOPIC = "adunn/sensor/light/zone1"
CMD_TOPIC = "adunn/control/zone1/cmd"
ACK_TOPIC = "adunn/control/zone1/ack"

SOURCE = "pi-01"
ZONE = "ZONE1"
AS7341_DEV_ID = "as7341_01"
BH1750_DEV_ID = "bh1750_01"

#AS7341 MEASUREMENT CONFIGURATION
AS7341_GAIN_X = adafruit_as7341.Gain.GAIN_64X
AS7341_GAIN = 64  # 64X gain; for database
AS7341_ATIME = 0
AS7341_ASTEP = 9999

#in memory publish buffer
MAX_BUFFERED_MSGS = 2000 # 2000 msgs @ 5s interval ~= 2.5 hours
publish_buffer = deque(maxlen=MAX_BUFFERED_MSGS)

def buffer_message(topic: str, payload_str: str, qos: int = 1, retain: bool = False) -> None:
    publish_buffer.append({
        "topic":topic,
        "payload": payload_str,
        "qos": qos,
        "retain": retain,
        "ts": time.time()
    })
    log_buffer_state()

def flush_buffer(client:mqtt.Client) -> int:
    sent = 0
    while publish_buffer and client.is_connected():
        msg = publish_buffer[0]
        try:
            info = client.publish(msg["topic"], msg["payload"], qos=msg["qos"], retain=msg["retain"])
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                break # stop flushing, retry later
            publish_buffer.popleft()
            sent += 1
        except Exception:
            break
    return sent

def log_buffer_state(force: bool = False) -> None:
    global _last_buffer_log_ts
    now = time.time()
    if force or (now - _last_buffer_log_ts >= 30):
        LOGGER.info(f"[PI MANAGER] Size= {len(publish_buffer)}, Max= MAX_BUFFERED_MSGs")
        _last_buffer_log_ts = now
def utc_time_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def calculate_integration_time_ms(atime: int, astep: int)->float:
    return ((atime+1)*(astep+1)*2.78)/1000

class PiManager:
    def __init__(self):
        self.i2c = None
        self.lux = None
        self.spec = None

        self.run_active = False
        self.shutdown_requested = False
        self.run_id = None
        self.sample_interval_s = 5
        self.seq = 0

    def init_sensors(self) -> tuple[bool, str]:
        try:
            if self.i2c is None:
                self.i2c = busio.I2C(board.SCL, board.SDA)
                LOGGER.info("[PI MANAGER] I2C Initialized!")
        except Exception as e:
            LOGGER.error(f"[PI MANAGER] I2C failed to initialize. {e}")
            return False, f"I2C failed to initialize: {e}"
        
        
        #BH1750
        try:
            self.lux = adafruit_bh1750.BH1750(self.i2c)
            LOGGER.info("[PI MANAGER] BH1750 has Initialized!")
        except Exception as e:
            self.lux = None
            LOGGER.warning(f"[PI MANAGER] BH1759 failed to initialize: {e}")
            #print(f"BH1750 failed to initialize: {e}")

        #AS7341
        try:
            self.spec = adafruit_as7341.AS7341(self.i2c)
            self.spec.gain = AS7341_GAIN_X
            self.spec.atime = AS7341_ATIME
            self.spec.astep = AS7341_ASTEP
            LOGGER.info("[PI MANAGER] AS7341 has Initialized!")
        except Exception as e:
            self.spec = None
            LOGGER.warning(f"[PI MANAGER] AS7341 failed to initalize: {e}")
            #print(f"AS7341 failed to initialize: {e}")

        if self.lux is None and self.spec is None:            
            return False, "No sensors initialized"
        elif self.lux is None and self.spec is not None:
            return True, "OK (AS7341 initialized, BH1750 not connected)"
        elif self.lux is not None and self.spec is None:
            return True, "OK (BH1750 initialized, AS7341 not connected)"
        else:
            return True, "OK (both sensors initialized)"
        
    def build_payload(self) -> str:
        #BH1750 lux
        if self.lux is None:
            bh1750_lux = None
        else:
            try:
                bh1750_lux = float(self.lux.lux)
            except Exception as e:
                LOGGER.warning(f"[PI MANAGER] BH1750 read error: {e}")
                print(f"BH1750 read error: {e}")
                bh1750_lux = None
        #AS7341 spectral (raw counts)
        if self.spec is None:
            as7341_415nm = None
            as7341_445nm = None
            as7341_480nm = None
            as7341_515nm = None
            as7341_555nm = None
            as7341_590nm = None
            as7341_630nm = None
            as7341_680nm = None
            as7341_clear = None
            as7341_nir = None
        else:
            try:
                as7341_415nm = int(self.spec.channel_415nm)
                as7341_445nm = int(self.spec.channel_445nm)
                as7341_480nm = int(self.spec.channel_480nm)
                as7341_515nm = int(self.spec.channel_515nm)
                as7341_555nm = int(self.spec.channel_555nm)
                as7341_590nm = int(self.spec.channel_590nm)
                as7341_630nm = int(self.spec.channel_630nm)
                as7341_680nm = int(self.spec.channel_680nm)
                as7341_clear = int(self.spec.channel_clear)
                as7341_nir = int(self.spec.channel_nir)
            except Exception as e:
                print(f"AS7341 read error: {e}")
                LOGGER.info(f"AS7341 read error: {3}")
                as7341_415nm = None
                as7341_445nm = None
                as7341_480nm = None
                as7341_515nm = None
                as7341_555nm = None
                as7341_590nm = None
                as7341_630nm = None
                as7341_680nm = None
                as7341_clear = None
                as7341_nir = None
            
        #calculate integration time for as7341
        as7341_it_ms = calculate_integration_time_ms(AS7341_ATIME, AS7341_ASTEP)

        #Build payload
        payload = {
            "ts": utc_time_now_iso(),
            "source": SOURCE,
            "zone": ZONE,
            "run_id": self.run_id,

            "as7341_dev_id": AS7341_DEV_ID,
            "bh1750_dev_id": BH1750_DEV_ID,

            #MEASUREMENT CONFIGURATION
            "as7341_gain": AS7341_GAIN,
            "as7341_atime": AS7341_ATIME,
            "as7341_astep": AS7341_ASTEP,
            "as7341_it_ms": as7341_it_ms,

            #BH1750
            "bh1750_lux": bh1750_lux,
            #AS7341 (raw counts)
            "as7341_415nm": as7341_415nm,
            "as7341_445nm": as7341_445nm,
            "as7341_480nm": as7341_480nm,
            "as7341_515nm": as7341_515nm,
            "as7341_555nm": as7341_555nm,
            "as7341_590nm": as7341_590nm,
            "as7341_630nm": as7341_630nm,
            "as7341_680nm": as7341_680nm,
            "as7341_clear": as7341_clear,
            "as7341_nir": as7341_nir,

            "run_seq": self.seq
        }
        return json.dumps(payload)
    
    def ack(self, client: mqtt.Client, msg: dict):
        client.publish(ACK_TOPIC, json.dumps(msg), qos=1, retain=False)

def main():
    manager = PiManager()
    client = mqtt.Client(
        client_id="pi-manager_zone1",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2    
    )

    if BROKER_USER and BROKER_PASSWORD:
        client.username_pw_set(BROKER_USER, BROKER_PASSWORD)
    else:
        raise RuntimeError("MQTT_USER / MQTT_PASSWORD missing in pi .env")

    def on_connect(client, userdata, flags, reason_code, properties=None):
        LOGGER.info(f"[MQTT] Connected with reason_code: {reason_code}")
        client.subscribe(CMD_TOPIC, qos=1)
        print(f"[MQTT] Subscribed to topic: {CMD_TOPIC}")

        flushed = flush_buffer(client)
        if flushed:
            LOGGER.info(f"[MQTT] Flushed {flushed} buffered sensor messages. Remaining={len(publish_buffer)}")
    
    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
        print(
            LOGGER.info(
            f"[MQTT] Disconnected (flags={disconnect_flags}, reason_code={reason_code}). "
            "Buffering until reconnect...")
        )
    
    def on_message(client, userdata, msg):
        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            cmd = json.loads(raw)
        except Exception as e:
            LOGGER.error(f"[CMD] invalid json:", raw)
            return
        
        ctype = str(cmd.get("type", "")).upper()
        run_id = cmd.get("run_id")

        if ctype == "START":
            if not run_id:
                manager.ack(client, {"type": "READY", "status": "ERROR", "detail": "missing run_id"})
                return
            
            manager.run_id = str(run_id)
            manager.sample_interval_s = int(cmd.get("sample_interval_s", 5))

            ok, detail = manager.init_sensors()
            if not ok:
                LOGGER.error(f"[PI MANAGER] {detail}")
                manager.run_active = False
                manager.ack(client, {
                    "type": "READY",
                    "run_id": manager.run_id,
                    "source": SOURCE,
                    "zone": ZONE,
                    "status": "ERROR",
                    "detail": detail
                })
                return
            LOGGER.warning(f"[PI MANAGER] {detail}")
            manager.seq = 0
            manager.run_active = True
            manager.ack(client, {
                "type": "READY",
                "run_id": manager.run_id,
                "source": SOURCE,
                "zone": ZONE,
                "status": "OK",
                "detail": detail
            })
            LOGGER.info(f"[PI MANAGER] Started run_id: {manager.run_id}, sample interval: {manager.sample_interval_s}s")
            print(f"[RUN] Started run_id: {manager.run_id}, sample interval: {manager.sample_interval_s}s")

        elif(ctype == "STOP"):
            if run_id and manager.run_id and str(run_id) != manager.run_id:
                manager.ack(client, {
                    "type": "STOPPED",
                    "run_id": manager.run_id,
                    "source": SOURCE,
                    "zone": ZONE,
                    "status": "ERROR",
                    "detail": f"run_id mismatch: received {run_id}, current {manager.run_id}"
                })
                LOGGER.error(f"run_id mismatch: received {run_id}, current {manager.run_id}")
                return
            manager.run_active = False
            stopped_run = manager.run_id
            manager.run_id = None
            manager.seq = 0
            manager.ack(client, {"type": "STOPPED", "run_id": stopped_run, "source": SOURCE, "zone": ZONE, "status": "OK"})
            LOGGER.info(f"[PI MANAGER] Stopped run_id: {stopped_run}")
            print(f"[RUN] Stopped run_id: {stopped_run}")
        elif ctype == "EXIT":
            if run_id and manager.run_id and str(run_id) != manager.run_id:
                manager.ack(client, {
                    "type": "EXITING",
                    "run_id": manager.run_id,
                    "source": SOURCE,
                    "zone": ZONE,
                    "status": "ERROR",
                    "detail": f"run_id mismatch: received {run_id}, current {manager.run_id}",
                })
                LOGGER.error(f"[PI MANAGER] run_id mismatch: received {run_id}, current {manager.run_id}")
                return
            manager.run_active = False
            manager.shutdown_requested = True
            stopped_run = manager.run_id
            manager.run_id = None
            manager.seq = 0
            manager.ack(client, {
                "type": "EXITING",
                "run_id": stopped_run,
                "source": SOURCE,
                "zone": ZONE,
                "status": "OK",
                "detail": "shutting down"
            })
            LOGGER.info(f"[PI MANAGER] Shutdown requested for run_id: {stopped_run}")
            print(f"[RUN] Shutdown requested for run_id: {stopped_run}")
        else:
            LOGGER.error(f"[PI MANAGER] Unknown Command Type: {cmd}")
            print(f"[CMD] unknown command type: {cmd}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    client.reconnect_delay_set(min_delay=1, max_delay=30)

    client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while not manager.shutdown_requested:
            if manager.run_active:
                if client.is_connected() and len(publish_buffer) > 0:
                    flushed = flush_buffer(client)
                    if flushed:
                        LOGGER.info(f"[PI MANAGER] Flushed {flushed} buffered messages during run. Remaining: {len(publish_buffer)}")
                        print(f"[MQTT] Flushed {flushed} buffered mssages during run. Remaining : {len(publish_buffer)}")
                payload = manager.build_payload()
                if client.is_connected():
                    try:
                        info = client.publish(DATA_TOPIC, payload, qos=1, retain=False)
                        if info.rc != mqtt.MQTT_ERR_SUCCESS:
                            buffer_message(DATA_TOPIC, payload, qos=1, retain=False)
                    except Exception as e:
                        LOGGER.error(f"[MQTT] Publish failed. Buffering. err: {e}")
                        buffer_message(DATA_TOPIC, payload, qos=1, retain=False)
                else:
                    buffer_message(DATA_TOPIC, payload, qos=1, retain=False)

                manager.seq += 1
                time.sleep(manager.sample_interval_s)

            else:
                time.sleep(0.2) # idle wait time

    except KeyboardInterrupt:
        LOGGER.warning("[PI MANAGER] User exit requested. shutting down")
        print("\n[RUN] User Exit requested. shutting down...")
        manager.run_active = False
        manager.shutdown_requested = True
    finally:
        client.loop_stop()
        client.disconnect()
        LOGGER.info("[PI MANAGER] Disconnected - exit complete")
        print("[RUN] Disconnected - exit complete.")

if __name__ == "__main__":
    main()    
