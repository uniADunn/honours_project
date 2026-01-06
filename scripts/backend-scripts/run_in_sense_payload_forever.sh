#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/incoming_sensor_payload.log"

mkdir -p "$LOG_DIR"

PYTHON="$PROJECT_DIR/.venv/Scripts/python.exe"
INGEST_SCRIPT="$SCRIPT_DIR/incoming_sensor_payload.py"

echo "[$(date -Is)] Supervisor starting..." | tee -a "$LOG_FILE"
echo "[$(date -Is)] PROJECT_DIR=$PROJECT_DIR" | tee -a "$LOG_FILE"
echo "[$(date -Is)] SCRIPT_DIR=$SCRIPT_DIR" | tee -a "$LOG_FILE"
echo "[$(date -Is)] PYTHON=$PYTHON" | tee -a "$LOG_FILE"
echo "[$(date -Is)] INGEST_SCRIPT=$INGEST_SCRIPT" | tee -a "$LOG_FILE"

while true; do
    echo "[$(date -Is)] Launching ingestion..." | tee -a "$LOG_FILE"
    cd "$PROJECT_DIR"
    "$PYTHON" -u "$INGEST_SCRIPT" >> "$LOG_FILE" 2>&1
    echo "[$(date -Is)] Ingestion exited. Restarting in 3..." | tee -a "$LOG_FILE"
    sleep 3
done
