# GPU worker for Laura's heavy analysis models (ASR first). The cuDNN runtime base lets
# faster-whisper's CTranslate2 CUDA backend find cuDNN; the host's nvidia default runtime
# grants the container GPU access. Falls back to CPU inside the container if CUDA is unusable.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY server.py .

ENV LAURA_RUNTIME_PORT=8896
EXPOSE 8896
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${LAURA_RUNTIME_PORT:-8896}"]
