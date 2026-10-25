FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt
COPY . /app
RUN useradd --uid 10001 --create-home agent && mkdir -p /app/data && chown -R agent:agent /app
USER agent
CMD ["python", "-m", "voice_agent.worker", "start"]
