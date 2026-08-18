FROM huggingface/transformers-pytorch-gpu:latest@sha256:4c7317881a534b22e18add49c925096fa902651fb0571c69f3cad58af3ea2c0f

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git ffmpeg libsndfile1 libgl1-mesa-glx libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev
ENV PATH="/opt/venv/bin:${PATH}"

COPY backend ./backend
COPY frontend ./frontend
COPY run.sh ./
RUN chmod +x run.sh

# models and data are mounted at runtime via volumes
VOLUME ["/app/models", "/app/data"]

EXPOSE 8010

ENTRYPOINT ["python", "backend/main.py"]
CMD ["--host", "0.0.0.0", "--port", "8010"]
