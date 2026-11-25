#!/usr/bin/env bash
# Start Local Ledger (and the OCR model server, if its files are present).
#   HOST=0.0.0.0 LEDGER_PASSWORD=... ./run.sh   reach the app from other devices on your network
#   OCR=0 ./run.sh          don't start the OCR server
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating Python environment..."
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  fi
fi
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8890}"
OCR_PORT="${OCR_PORT:-8892}"
OCR_MODEL_DIR="${OCR_MODEL_DIR:-$HOME/models/PaddleOCR-VL-1.6-GGUF}"
export LEDGER_URL="${LEDGER_URL:-http://127.0.0.1:$PORT}"
mkdir -p data

OCR_PID=""
cleanup() { [ -n "$OCR_PID" ] && kill "$OCR_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
if [ "${OCR:-1}" = "1" ]; then
  if [ ! -f "$OCR_MODEL_DIR/PaddleOCR-VL-1.6-GGUF.gguf" ]; then
    :   # no OCR model here: photos are read by the main model only
  elif (exec 3<>"/dev/tcp/127.0.0.1/$OCR_PORT") 2>/dev/null; then
    echo "OCR server already running on port $OCR_PORT"
  elif [ -f "$OCR_MODEL_DIR/PaddleOCR-VL-1.6-GGUF.gguf" ]; then
    echo "Starting OCR server (PaddleOCR-VL) on port $OCR_PORT - log: data/ocr-server.log"
    OCR_MODEL_DIR="$OCR_MODEL_DIR" OCR_PORT="$OCR_PORT" ./run_ocr.sh > data/ocr-server.log 2>&1 &
    OCR_PID=$!
  fi
fi

echo "Local Ledger running at http://$HOST:$PORT  (first start: a setup screen asks for your country and model)"
.venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
