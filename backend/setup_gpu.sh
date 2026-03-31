#!/bin/bash
set -e

echo "=========================================================="
echo "🚀 ĐANG KHỞI TẠO MÔI TRƯỜNG CUDA 12 CHO RTX 3090 Ti..."
echo "=========================================================="

# 1. Tải toàn bộ thư viện nhận diện GPU và lõi toán học CUDA 12
echo "[1/4] Đang cài đặt cuDNN, cuBLAS, cuRAND, cuFFT, cuSPARSE..."
uv pip install onnxruntime-gpu --force-reinstall
uv pip install nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-curand-cu12 nvidia-cufft-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-cuda-runtime-cu12

# 2. Xóa các cấu hình cũ (tránh ghi chồng chéo sinh rác trong .bashrc)
echo "[2/4] Đang định cấu hình biến môi trường LD_LIBRARY_PATH..."
sed -i '/LD_LIBRARY_PATH.*nvidia/d' ~/.bashrc

# 3. Tạo đường dẫn gộp tất cả thư mục lib của NVIDIA bằng lệnh find tự động
export NVIDIA_DIR="$HOME/libero_mail_scanner/backend/.venv/lib/python3.12/site-packages/nvidia"

NVIDIA_PATHS=$(find "$NVIDIA_DIR" -maxdepth 2 -type d -name "lib" 2>/dev/null | paste -sd ':' -)
echo "export LD_LIBRARY_PATH=\"$NVIDIA_PATHS:\$LD_LIBRARY_PATH\"" >> ~/.bashrc

# 4. Áp dụng ngay biến môi trường vào phiên làm việc hiện tại
export LD_LIBRARY_PATH="$NVIDIA_PATHS:$LD_LIBRARY_PATH"

echo "[3/4] Môi trường CUDA đã sẵn sàng! Đường dẫn: $NVIDIA_PATHS"

# 5. Khởi động lại hệ thống bằng PM2
echo "[4/4] Khởi động lại hệ thống Libero Backend..."
if command -v pm2 &> /dev/null; then
    pm2 restart libero_backend --update-env
    echo "=========================================================="
    echo "✅ HOÀN TẤT! Máy AI đã ăn được cấu hình màn hình RTX 3090 Ti."
    echo "=========================================================="
    echo "Gõ lệnh: 'pm2 logs libero_backend' để xem tốc độ cắn GPU 15 workers!"
else
    echo "⚠️ Không tìm thấy PM2. Vui lòng tự chạy backend thủ công bằng lệnh uv run uvicorn."
fi
