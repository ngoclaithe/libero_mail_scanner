#!/bin/bash
set -e

echo "=> Đang cài đặt Nginx..."
sudo apt update
sudo apt install -y nginx curl

echo "=> Đang kiểm tra Node.js và PM2..."
if ! command -v npm &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt install -y nodejs
fi
if ! command -v pm2 &> /dev/null; then
    sudo npm install -g pm2
fi

echo "=> Đang kiểm tra uv..."
if ! command -v uv &> /dev/null; then
    curl -4 -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
fi

echo "=> Đang chạy uv sync..."
uv sync

echo "=> Đang cài đặt thư viện Onnxruntime-GPU..."
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
