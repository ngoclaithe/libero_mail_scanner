#!/bin/bash
set -e

echo "=> Đang cài đặt Nginx và các công cụ bổ trợ (zbar)..."
sudo apt update
sudo apt install -y nginx curl libzbar0

echo "=> Đang kiểm tra Node.js và PM2..."
if ! command -v npm &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt install -y nodejs
fi
if ! command -v pm2 &> /dev/null; then
    sudo npm install -g pm2
fi

echo "=> Đang kiểm tra uv..."
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
if ! command -v uv &> /dev/null; then
    echo "=> Tải qua PIP3 để chống kẹt mạng GitHub..."
    sudo apt install -y python3-pip
    pip3 install uv --break-system-packages || python3 -m pip install uv --break-system-packages
fi

echo "=> Đang chạy uv sync..."
uv sync

echo "=> Đang cài đặt thư viện Onnxruntime-GPU..."
uv pip install onnxruntime-gpu --force-reinstall
uv pip install nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-curand-cu12 nvidia-cufft-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-cuda-runtime-cu12

sed -i '/LD_LIBRARY_PATH.*nvidia/d' ~/.bashrc

export NVIDIA_DIR="$(pwd)/.venv/lib/python3.12/site-packages/nvidia"
NVIDIA_PATHS=$(find "$NVIDIA_DIR" -maxdepth 2 -type d -name "lib" 2>/dev/null | paste -sd ':' -)

echo "export LD_LIBRARY_PATH=\"$NVIDIA_PATHS:\$LD_LIBRARY_PATH\"" >> ~/.bashrc
export LD_LIBRARY_PATH="$NVIDIA_PATHS:$LD_LIBRARY_PATH"

if command -v pm2 &> /dev/null; then
    echo "=> Đang đăng ký và khởi động libero_backend vào PM2..."
    UV_BIN="$(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
    pm2 delete libero_backend 2>/dev/null || true
    pm2 start "export LD_LIBRARY_PATH=\"$NVIDIA_PATHS:\$LD_LIBRARY_PATH\" && $UV_BIN run uvicorn main:app --host 0.0.0.0 --port 8000" --name "libero_backend"
    pm2 save
fi

echo "=> Cấp phép tải Cloudflared (nếu chưa có)..."
if ! command -v cloudflared &> /dev/null; then
    curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    sudo dpkg -i cloudflared.deb
    rm cloudflared.deb
fi

echo "=> Viết file cấu trúc tự động cho Tunnel..."
mkdir -p "$HOME/.cloudflared"
cat << 'EOF' > "$HOME/.cloudflared/config.yml"
tunnel: 037cc209-ee74-4545-b610-e1bf1591fd5f
credentials-file: /home/ezycloudx-admin/.cloudflared/037cc209-ee74-4545-b610-e1bf1591fd5f.json

ingress:
  - hostname: gpu-api-libero.kekhaidichvucong.to
    service: http://localhost:8000
  - service: http_status:404
EOF

if command -v pm2 &> /dev/null; then
    echo "=> Đang cài đặt cắm Tunnel chạy ngầm qua PM2..."
    pm2 delete cf_tunnel 2>/dev/null || true
    pm2 start "cloudflared tunnel run 037cc209-ee74-4545-b610-e1bf1591fd5f" --name "cf_tunnel"
    pm2 save
fi
