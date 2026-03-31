#!/bin/bash
set -e

uv pip install onnxruntime-gpu --force-reinstall
uv pip install nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-curand-cu12 nvidia-cufft-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-cuda-runtime-cu12

sed -i '/LD_LIBRARY_PATH.*nvidia/d' ~/.bashrc

export NVIDIA_DIR="$HOME/libero_mail_scanner/backend/.venv/lib/python3.12/site-packages/nvidia"
NVIDIA_PATHS=$(find "$NVIDIA_DIR" -maxdepth 2 -type d -name "lib" 2>/dev/null | paste -sd ':' -)

echo "export LD_LIBRARY_PATH=\"$NVIDIA_PATHS:\$LD_LIBRARY_PATH\"" >> ~/.bashrc
export LD_LIBRARY_PATH="$NVIDIA_PATHS:$LD_LIBRARY_PATH"

if command -v pm2 &> /dev/null; then
    pm2 restart libero_backend --update-env
fi
