#!/usr/bin/env bash
# Optional: start the OCR model (PaddleOCR-VL 1.6, GGUF) as a small llama.cpp server. The ledger then reads every
# photo twice - once through this transcription, once with the main model - and flags amounts that disagree.
# Needs llama-server (llama.cpp) and the two model files from PaddlePaddle/PaddleOCR-VL-1.6-GGUF.
#   OCR_MODEL_DIR  folder with the .gguf files          OCR_GPU   which GPU (nvidia-smi index, default 0)
#   OCR_HOST       address to listen on (default 127.0.0.1; 0.0.0.0 if the ledger runs on another machine)
#   OCR_MMPROJ_ON_GPU=1  also put the image encoder on the GPU (default: CPU, to leave room for the main model)
set -euo pipefail
MODEL_DIR="${OCR_MODEL_DIR:-$HOME/models/PaddleOCR-VL-1.6-GGUF}"
LLAMA_SERVER="${LLAMA_SERVER:-$(command -v llama-server || echo "$HOME/.unsloth/llama.cpp/llama-server")}"   # llama.cpp (Unsloth Studio ships one)
PORT="${OCR_PORT:-8892}"
# Only show the chosen GPU to this process, so CUDA doesn't reserve memory on the other card too.
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="${OCR_GPU:-0}"
EXTRA=()
[ "${OCR_MMPROJ_ON_GPU:-0}" = "1" ] || EXTRA+=(--no-mmproj-offload)
exec "$LLAMA_SERVER" \
  -m "$MODEL_DIR/PaddleOCR-VL-1.6-GGUF.gguf" \
  --mmproj "$MODEL_DIR/PaddleOCR-VL-1.6-GGUF-mmproj.gguf" \
  --alias paddleocr-vl --host "${OCR_HOST:-127.0.0.1}" --port "$PORT" \
  --device CUDA0 -ngl 99 -c 8192 --parallel 1 --temp 0 "${EXTRA[@]}"
