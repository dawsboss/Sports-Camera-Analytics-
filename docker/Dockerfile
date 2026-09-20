# One image for the API and the worker; the compose file picks the command.
# The M3 worker image adds CUDA, PyTorch, Ultralytics and ByteTrack on top of
# this; S0 and S1 need only ffmpeg and OpenCV.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sideline ./sideline
RUN pip install --no-cache-dir ".[service]"

ENV SIDELINE_DATA_DIR=/data
VOLUME ["/data"]

CMD ["sideline", "serve", "--host", "0.0.0.0", "--port", "8000"]
