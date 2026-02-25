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

from shared import spectral_conversion
from shared.spectral_conversion import bands_wm2_from_counts, safe_sum
load_dotenv()

_last_buffer_log_ts = 0.0

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

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

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
DECISION_TOPIC = "adunn/control/zone1/decision"

SOURCE = "pi-01"
ZONE = "ZONE1"
AS7341_DEV_ID = "as7341_01"
BH1750_DEV_ID = "bh1750_01"

#AS7341 MEASUREMENT CONFIGURATION
AS7341_GAIN_X = adafruit_as7341.Gain.GAIN_64X
AS7341_GAIN = 64  # 64X gain; for database
AS7341_ATIME = 0
AS7341_ASTEP = 9999

WM2_EPS = 0.01  # BELOW THIS, TREAT AS DARK
TARGET_J_EPS = 50.0 #BELOW THIS, TREAT TARGET AS ZERO
OUTPUT_STEP_PCT = 5.0 # VIRTUAL STEP PER SAMPLE

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
        LOGGER.info(
            f"[PI MANAGER] Buffer size={len(publish_buffer)}/{MAX_BUFFERED_MSGS}"
        )
        _last_buffer_log_ts = now

def utc_time_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def parse_iso_utc(strDate: str) -> datetime:
    if not strDate:
        raise ValueError("empty timestamp")

    # Normalize common UTC suffix used by JSON payloads.
    raw = str(strDate).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        # Treat naive timestamps as UTC to keep run timing deterministic.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def calculate_integration_time_ms(atime: int, astep: int)->float:
    return ((atime+1)*(astep+1)*2.78)/1000

def compute_slot(run_start_ts: datetime, now_ts: datetime)-> int:
    elapsed_s = (now_ts - run_start_ts).total_seconds()
    if elapsed_s < 0:
        return 0
    return int(elapsed_s //3600)

def decide_energy(pred_j: float, tgt_j: float, tolerance_pct: float) -> str:
    lower = tgt_j * (1.0 - (tolerance_pct /100.0))
    upper = tgt_j * (1.0 + (tolerance_pct /100.0))

    if pred_j < lower:
        return "INCREASE"
    elif pred_j > upper:
        return "DECREASE"
    else:
        return "HOLD"

def clamp_action_decision(
        raw_decision: str,
        measured_wm2: float,
        target_j: float,
        output_level_pct: float
    ) -> tuple[str, list[str]]:
    reasons: list[str] = []
    final = raw_decision

    if raw_decision == "DECREASE" and measured_wm2 <= WM2_EPS:
        final = "HOLD"
        reasons.append("measured irradiance is below threshold, treating as dark.")
    
    if target_j <= TARGET_J_EPS and measured_wm2 <=WM2_EPS:
        if final != "HOLD":
            final = "HOLD"
            reasons.append("target energy is effectively zero and dark.")

    #virtual output saturation clamps
    # if raw_decision == "DECREASE" and output_level_pct <= 0.0:
    #     final = "HOLD"
    #     reasons.append("output already at 0%, cannot decrease further.")

    # if raw_decision == "INCREASE" and output_level_pct >= 100.0:
    #     final = "HOLD"
    #     reasons.append("output already at 100%, cannot increase further.")

    return final, reasons

class PiManager:
    def __init__(self):
        self.i2c = None
        self.lux = None
        self.spec = None

        
        self.run_active = False
        self.shutdown_requested = False
        self.run_id = None
        self.run_start_ts: datetime | None = None
        self.current_slot: int | None = None
        self.last_sample_ts: datetime | None = None
        self.accumulated_joules_by_band = {
            "blue": 0.0,
            "green": 0.0,
            "red": 0.0
        }
        self.targets_by_slot: list | None = None
        self.sample_interval_s = 5
        self.seq = 0
        self.decision_policy: dict = {
            "tolerance_pct": 20.0,
            "min_samples_before_decision": 3
        }
        self.output_level_pct = {
            "blue": 20.0,
            "green": 20.0,
            "red": 20.0
        }

    def reset_run(self) -> None:
        self.run_active = False
        self.run_id = None
        self.targets_by_slot = None
        self.run_start_ts = None
        self.seq = 0
        self.current_slot = None
        self.last_sample_ts = None
        self.accumulated_joules_by_band = {
            "blue": 0.0,
            "green": 0.0,
            "red": 0.0
        }
        self.output_level_pct = {
            "blue": 0.0,
            "green": 0.0,
            "red": 0.0
        }

    def init_run(self, run_id: str, run_start_ts: datetime, targets_by_slot: list,
                 decision_policy: dict, sample_interval_s: int) -> None:
        
        self.run_id = str(run_id)
        self.sample_interval_s = sample_interval_s
        self.run_start_ts = run_start_ts
        self.targets_by_slot = targets_by_slot
        self.decision_policy = decision_policy

        self.seq = 0
        self.run_active = False
        self.current_slot = None
        self.last_sample_ts = None
        self.accumulated_joules_by_band = {
            "blue": 0.0,
            "green": 0.0, 
            "red": 0.0
        }
        self.output_level_pct = {
            "blue": 0.0,
            "green": 0.0,
            "red": 0.0
        }        

    def parse_start_cmd(self, cmd: dict) -> tuple[bool, str, dict]:
        run_id = cmd.get("run_id")
        if not run_id:
            return False, "missing run_id", {}
        
        try:
            run_start_ts = parse_iso_utc(cmd.get("run_start_ts"))
        except Exception as e:
            return False, f"invalid run_start_ts: {e}", {}
        
        # targets = cmd.get("targets_by_slot")
        # if not isinstance(targets, list) or len(targets) == 0:
        #     return False, "missing or invalid targets_by_slot (must not be an empty list)", {}
        
        sample_interval_s = int(cmd.get("sample_interval_s", 5)) # default to 5s interval
        decision_policy = cmd.get("decision_policy") or {"tolerance_pct": 5.0, "min_samples_before_decision": 6}

        return True, "OK", {
            "run_id": str(run_id),
            "run_start_ts": run_start_ts,
            #"targets_by_slot": targets,
            "sample_interval_s": sample_interval_s,
            "decision_policy": decision_policy
        }
    
    def handle_cmd(self, client: mqtt.Client, cmd: dict) -> None:
        ctype = str(cmd.get("type", "")).upper()
        if ctype == "START":
            self.handle_start_cmd(client, cmd)
        elif ctype == "STOP":
            self.handle_stop_cmd(client, cmd)
        elif ctype == "EXIT":
            self.handle_exit_cmd(client, cmd)
        else:
            LOGGER.error(f"[PI MANAGER] Unknown Command Type: {cmd}")
            print(f"[CMD] Unknown Command Type: {cmd}")

    def handle_start_cmd(self, client: mqtt.Client, cmd: dict) -> None:
        ok, detail, parsed = self.parse_start_cmd(cmd)
        run_id = parsed.get("run_id") if parsed else cmd.get("run_id")

        LOGGER.info(f"[PI MANAGER] Received START command: {run_id}, payload: {cmd}")

        if not ok:
            self.ack(client, {
                "type": "READY",
                "status": "ERROR",
                "detail": detail
            })
            return
        self.init_run(
            run_id = parsed["run_id"],
            sample_interval_s = parsed["sample_interval_s"],
            run_start_ts = parsed["run_start_ts"],
            #targets_by_slot= parsed["targets_by_slot"],
            decision_policy = parsed["decision_policy"]
        )

        LOGGER.info(f"[SCHEDULER] Received tartgets_by_slot: {len(self.targets_by_slot)}, first_ref_ts: {self.targets_by_slot[0].get('ref_ts')}")

        ok_sensors, sensor_detail = self.init_sensors()
        if not ok_sensors:
            LOGGER.info(f"[PI MANAGER] {sensor_detail}")
            self.reset_run()
            self.ack(client, {
                "type": "READY",
                "run_id": parsed["run_id"],
                "source": SOURCE,
                "zone": ZONE,
                "status": "ERROR",
                "detail": sensor_detail
            })
            return
        LOGGER.warning(f"[PI MANAGER] {sensor_detail}")

        self.run_active = True

        self.ack(client, {
            "type": "READY",
            "run_id": self.run_id,
            "run_start_ts": self.run_start_ts.isoformat().replace("+00:00", "Z"),
            "source": SOURCE,
            "zone": ZONE,
            "status": "OK",
            "detail": sensor_detail
        })

        LOGGER.info(f"[PI MANAGER] Started run_id: {self.run_id}, sample interval: {self.sample_interval_s}s")
        print(f"[RUN] Started run_id: {self.run_id}, sample interval: {self.sample_interval_s}s")

    def _check_run_id_match(self, cmd_run_id: str) -> tuple[bool, str]:
        if cmd_run_id and self.run_id and str(cmd_run_id) != self.run_id:
            return False, f"run_id mismatch: received {cmd_run_id}, current{self.run_id}"
        return True, "OK"
    
    def handle_stop_cmd(self, client: mqtt.Client, cmd: dict) -> None:
        ok, detail = self._check_run_id_match(cmd.get("run_id"))
        if not ok:
            self.ack(client, {
                "type": "STOPPED", 
                "run_id": self.run_id,
                "source": SOURCE,
                "zone": ZONE,
                "status": "ERROR",
                "detail": detail
            })
            LOGGER.error(f"[PI MANAGER] {detail}")
            return
        
        stopped_run = self.run_id
        self.reset_run()
        
        self.ack(client, {
            "type": "STOPPED",
            "run_id": stopped_run,
            "source": SOURCE,
            "zone": ZONE,
            "status": "OK",
            "detail": "run stopped successfully"
        })
        LOGGER.info(f"[PI MANAGER] stopped run_id: {stopped_run}")
        print(f"[RUN] Stopped run_id: {stopped_run}")

    def handle_exit_cmd(self, client: mqtt.Client, cmd: dict) -> None:
        ok, detail = self._check_run_id_match(cmd.get("run_id"))
        if not ok:
            self.ack(client, {
                "type": "EXITING",
                "run_id": self.run_id,
                "source": SOURCE,
                "zone": ZONE,
                "status": "ERROR",
                "detail": detail
            })
            LOGGER.error(detail)
            return
        
        stopped_run = self.run_id
        self.reset_run()
        self.shutdown_requested = True
        
        self.ack(client, {
            "type": "EXITING",
            "run_id": stopped_run,
            "source": SOURCE,
            "zone": ZONE,
            "status": "OK",
            "detail": "shutting down"
        })
        LOGGER.info(f"[PI MANAGER] Shutdown requested for run_id: {stopped_run}")
        print(f"[RUN] Shutdown requested for run_id: {stopped_run}")

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
        
    def build_payload(self) -> tuple[str, dict]:
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
                LOGGER.warning(f"[PI MANAGER] AS7341 read error: {e}")
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
        raw_channels = {
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
            "as7341_gain": AS7341_GAIN,
            "as7341_it_ms": as7341_it_ms,
            "bh1750_lux": bh1750_lux,
        }
        return json.dumps(payload), raw_channels
    
    def ack(self, client: mqtt.Client, msg: dict):
        payload = json.dumps(msg)
        if not client.is_connected():
            LOGGER.error(f"[ACK] Not connected. cannot publish ack: payload: {payload}")
            return
        info = client.publish(ACK_TOPIC, payload, qos=1, retain = False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error(f"[ACK] Publish failed rc={info.rc}. payload: {payload}")
        else:
            LOGGER.info(f"[ACK] published to {ACK_TOPIC}: payload: {payload}")

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
        LOGGER.info(
            f"[MQTT] Disconnected (flags={disconnect_flags}, reason_code={reason_code}). "
            "Buffering until reconnect..."
        )
    
    def on_message(client, userdata, msg):
        raw = msg.payload.decode("utf-8", errors="replace")
        try:
            cmd = json.loads(raw)
        except Exception:
            LOGGER.error(f"[CMD] invalid json: {raw}")
            return
        
        manager.handle_cmd(client, cmd)


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
                        print(f"[MQTT] Flushed {flushed} buffered messages during run. Remaining : {len(publish_buffer)}")
                now_ts = datetime.now(timezone.utc)

                if manager.last_sample_ts is None:
                    dt = manager.sample_interval_s
                else:
                    dt = (now_ts - manager.last_sample_ts).total_seconds()

                manager.last_sample_ts = now_ts
                

                if manager.run_start_ts is None:
                    LOGGER.error("[SLOT] run_start_ts is None but run_active = True. Forcing STOP state.")
                    manager.run_active = False
                    continue
                slot = compute_slot(manager.run_start_ts, now_ts)

                if manager.current_slot != slot:
                    LOGGER.info(f"[SLOT] slot changed {manager.current_slot} -> {slot}")
                    manager.current_slot = slot
                    manager.accumulated_joules_by_band = {
                        "blue": 0.0,
                        "green": 0.0,
                        "red": 0.0
                    }
                LOGGER.info(f"[SLOT] now: {now_ts.isoformat()}, run_start: {manager.run_start_ts.isoformat()}, slot: {slot}")
                
                payload, raw_channels = manager.build_payload()
                decision_out = {"blue": "HOLD", "green": "HOLD", "red": "HOLD"}
                decision_reasons = {"blue": [], "green":[], "red":[]}
                notes = None
                ref_ts = None

                tgt_blue = 0.0
                tgt_green = 0.0
                tgt_red = 0.0

                measured_blue = 0.0
                measured_green = 0.0
                measured_red = 0.0

                tolerance = float(manager.decision_policy.get("tolerance_pct", 5.0))
                min_n = int(manager.decision_policy.get("min_samples_before_decision", 3))

                remaining_s_in_slot = 0.0
                pred_blue_j = 0.0
                pred_green_j = 0.0
                pred_red_j = 0.0
                
                tgt_blue_j = 0.0
                tgt_green_j = 0.0
                tgt_red_j = 0.0

                if manager.targets_by_slot is None:
                    notes = "HOLD: targets_by_slot is None"
                elif slot < 0 or slot >= len(manager.targets_by_slot):
                    notes = f"HOLD: slot out of range (slot: {slot}, len: {len(manager.targets_by_slot)})"
                else:
                    trow = manager.targets_by_slot[slot]
                    ref_ts = trow.get("ref_ts")

                    tgt = trow.get("target", {}) or {}

                    tgt_blue = float(tgt.get("blue", 0.0))
                    tgt_green = float(tgt.get("green", 0.0))
                    tgt_red = float(tgt.get("red", 0.0))

                    bands = bands_wm2_from_counts(
                        c415 = raw_channels.get("as7341_415nm"),
                        c445 = raw_channels.get("as7341_445nm"),
                        c480 = raw_channels.get("as7341_480nm"),
                        c515 = raw_channels.get("as7341_515nm"),
                        c555 = raw_channels.get("as7341_555nm"),
                        c590 = raw_channels.get("as7341_590nm"),
                        c630 = raw_channels.get("as7341_630nm"),
                        c680 = raw_channels.get("as7341_680nm"),
                        gain_meas = float(raw_channels.get("as7341_gain") or AS7341_GAIN),
                        it_meas_ms = float(raw_channels.get("as7341_it_ms") or 0),
                    )

                    measured_blue = float(bands.get("blue_W_m2") or 0.0)
                    measured_green = float(bands.get("green_W_m2") or 0.0)
                    measured_red = float(bands.get("red_W_m2") or 0.0)

                    manager.accumulated_joules_by_band["blue"] += (measured_blue * dt)
                    manager.accumulated_joules_by_band["green"] += (measured_green * dt)
                    manager.accumulated_joules_by_band["red"] += (measured_red * dt)

                    tgt_blue_j = tgt_blue * 1_000_000.0
                    tgt_green_j = tgt_green * 1_000_000.0
                    tgt_red_j = tgt_red * 1_000_000.0

                    #seconds remaining in current slot (hour)
                    elapsed_s_in_slot = (now_ts - manager.run_start_ts).total_seconds() % 3600.0
                    remaining_s_in_slot = max(0.0, 3600.0 - elapsed_s_in_slot)

                    #predicted total energy by the end of the hour assuming curreent irradiance holds
                    pred_blue_j = manager.accumulated_joules_by_band["blue"] + (measured_blue * remaining_s_in_slot)
                    pred_green_j = manager.accumulated_joules_by_band["green"] + (measured_green * remaining_s_in_slot)
                    pred_red_j = manager.accumulated_joules_by_band["red"] + (measured_red * remaining_s_in_slot)

                    LOGGER.info(
                        f"[PREDICTION] slot: {slot}, remaining_s_in_slot: {remaining_s_in_slot:.1f}s "
                        f"proj_j(b/g/r)=({pred_blue_j:.4f}, {pred_green_j:.4f}, {pred_red_j:.4f}) "
                        f"tgt_j(b/g/r)=({tgt_blue_j:.4f}, {tgt_green_j:.4f}, {tgt_red_j:.4f}) "
                    )
                    LOGGER.info(
                        f"[ACCUM] slot: {slot} "
                        f"accumulated_Joules(b/g/r)=("
                        f"{manager.accumulated_joules_by_band['blue']:.4f},"
                        f"{manager.accumulated_joules_by_band['green']:.4f},"
                        f"{manager.accumulated_joules_by_band['red']:.4f}) "
                        f"dt: {dt:.4f}s"
                    )

                    if manager.seq < min_n:
                        notes = f"HOLD: seq: {manager.seq} < min_samples_before_decision {min_n}"
                    else:
                        raw_decision = {
                            "blue": decide_energy(pred_blue_j, tgt_blue_j, tolerance),
                            "green": decide_energy(pred_green_j, tgt_green_j, tolerance),
                            "red": decide_energy(pred_red_j, tgt_red_j, tolerance)
                        }

                        decision_out = {}
                        decision_reasons = {}
                        
                        band_map = {
                            "blue": (measured_blue, tgt_blue_j),
                            "green": (measured_green, tgt_green_j),
                            "red": (measured_red, tgt_red_j)
                        }

                        for band, (meas_wm2, target_j) in band_map.items():
                            out_lvl = float(manager.output_level_pct.get(band, 0.0))

                            final, reasons = clamp_action_decision(
                                raw_decision = raw_decision[band],
                                measured_wm2 = meas_wm2,
                                target_j = target_j,
                                output_level_pct = out_lvl,
                            )

                            if final == "INCREASE":
                                out_lvl = min(100.0, out_lvl + OUTPUT_STEP_PCT)
                            elif final == "DECREASE":
                                out_lvl = max(0.0, out_lvl - OUTPUT_STEP_PCT)
                            
                            manager.output_level_pct[band] = out_lvl
                            decision_out[band] = final
                            decision_reasons[band] = reasons
                        
                LOGGER.info(
                    f"[DECISION] slot={slot}, ref_ts={ref_ts}, notes: {notes} "
                    f"remaining_s_in_slot: {remaining_s_in_slot:.4f}s "
                    f"measured_W_m2(b/g/r): ({measured_blue:.4f}, {measured_green:.4f}, {measured_red:.4f}) "
                    f"accumulated_j(b/g/r): ("
                    f"{manager.accumulated_joules_by_band['blue']:.4f},"
                    f"{manager.accumulated_joules_by_band['green']:.4f},"
                    f"{manager.accumulated_joules_by_band['red']:.4f}) "
                    f"predicted_j(b/g/r): ({pred_blue_j:.4f}, {pred_green_j:.4f}, {pred_red_j:.4f}) "
                    f"target_j(b/g/r): ({tgt_blue_j:.4f}, {tgt_green_j:.4f}, {tgt_red_j:.4f}) "
                    f"decision={decision_out}"
                )

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

                decision_payload = {
                    "type": "DECISION",
                    "source": SOURCE,
                    "zone": ZONE,
                    "run_id": manager.run_id,
                    "run_seq": manager.seq,
                    "ts": now_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "slot": slot,
                    "ref_ts": ref_ts,
                    "policy": {
                        "tolerance_pct": tolerance,
                        "min_samples_before_decision": min_n,
                    },
                    "bands": {
                        "blue": {
                            "measured_wm2": measured_blue,
                            "target_mj_m2_hr": tgt_blue,
                            "target_j_m2_slot_total": tgt_blue_j,
                            "accumulated_j_m2_slot": manager.accumulated_joules_by_band["blue"],
                            "predicted_j_m2_slot": pred_blue_j,
                            
                            "decision": decision_out["blue"],
                            "decision_raw": raw_decision.get("blue") if manager.seq >= min_n else "HOLD",
                            "decision_reasons": decision_reasons.get("blue", []),
                            "output_level_pct": manager.output_level_pct["blue"]
                        },
                        "green": {
                            "measured_wm2": measured_green,
                            "target_mj_m2_hr": tgt_green,
                            "target_j_m2_slot_total": tgt_green_j,
                            "accumulated_j_m2_slot": manager.accumulated_joules_by_band["green"],
                            "predicted_j_m2_slot": pred_green_j,
                            "decision": decision_out["green"],
                            "decision_raw": raw_decision.get("green") if manager.seq >= min_n else "HOLD",
                            "decision_reasons": decision_reasons.get("green", []),
                            "output_level_pct": manager.output_level_pct["green"]
                        },
                        "red": {
                            "measured_wm2": measured_red,
                            "target_mj_m2_hr": tgt_red,
                            "target_j_m2_slot_total": tgt_red_j,
                            "accumulated_j_m2_slot": manager.accumulated_joules_by_band["red"],
                            "predicted_j_m2_slot": pred_red_j,
                            "decision": decision_out["red"],
                            "decision_raw": raw_decision.get("red") if manager.seq >= min_n else "HOLD",
                            "decision_reasons": decision_reasons.get("red", []),
                            "output_level_pct": manager.output_level_pct["red"]
                        },
                    },
                }

                if notes:
                    decision_payload["notes"] = notes

                decision_json = json.dumps(decision_payload)

                if client.is_connected():
                    try:
                        info = client.publish(DECISION_TOPIC, decision_json, qos=1, retain=False)
                        if info.rc != mqtt.MQTT_ERR_SUCCESS:
                            buffer_message(DECISION_TOPIC, decision_json, qos=1, retain=False)
                    except Exception as e:
                        LOGGER.error(f"[MQTT] Publish failed for decision. Buffering. err: {e}")
                        buffer_message(DECISION_TOPIC, decision_json, qos=1, retain=False)
                else:
                    buffer_message(DECISION_TOPIC, decision_json, qos=1, retain=False)

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
