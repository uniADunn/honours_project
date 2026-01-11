import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import board
import busio
import adafruit_bh1750
import adafruit_as7341

import os
from dotenv import load_dotenv
load_dotenv()

#MQTT CONFIGURATION
BROKER_HOST = os.getenv("MQTT_HOST")
BROKER_PORT = int(os.getenv("MQTT_PORT"))

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
        except Exception as e:
            return False, f"I2C failed to initialize: {e}"
        
        #BH1750
        try:
            self.lux = adafruit_bh1750.BH1750(self.i2c)
        except Exception as e:
            self.lux = None
            print(f"BH1750 failed to initialize: {e}")

        #AS7341
        try:
            self.spec = adafruit_as7341.AS7341(self.i2c)
            self.spec.gain = AS7341_GAIN_X
            self.spec.atime = AS7341_ATIME
            self.spec.astep = AS7341_ASTEP
        except Exception as e:
            self.spec = None
            print(f"AS7341 failed to initialize: {e}")

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
    client = mqtt.Client(client_id="pi-manager_zone1")

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f"[MQTT] Connected with reason_code: {reason_code}")
        client.subscribe(CMD_TOPIC)
        print(f"[MQTT] Subscribed to topic: {CMD_TOPIC}")
    
    def on_message(client, userdata, msg):
        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            cmd = json.loads(raw)
        except Exception as e:
            print(f"[CMD] invalid json:", raw)
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
                return
            manager.run_active = False
            stopped_run = manager.run_id
            manager.run_id = None
            manager.seq = 0
            manager.ack(client, {"type": "STOPPED", "run_id": stopped_run, "source": SOURCE, "zone": ZONE, "status": "OK"})
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
            print(f"[RUN] Shutdown requested for run_id: {stopped_run}")
        else:
            print(f"[CMD] unknown command type: {cmd}")

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while not manager.shutdown_requested:
            if manager.run_active:
                payload = manager.build_payload()
                info = client.publish(DATA_TOPIC, payload, qos=1, retain=False)
                info.wait_for_publish()
                manager.seq += 1
                time.sleep(manager.sample_interval_s)
            else:
                time.sleep(0.2) # idle wait
    except KeyboardInterrupt:
        print("\n[RUN] User Exit requested. shutting down...")
        manager.run_active = False
        manager.shutdown_requested = True
    finally:
        client.loop_stop()
        client.disconnect()
        print("[RUN] Disconnected - exit complete.")

if __name__ == "__main__":
    main()    