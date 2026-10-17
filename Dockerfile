FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
# world-writable so the app can run as any user (PUID/PGID); a new named volume inherits this
RUN mkdir /data && chmod 777 /data
ENV DATA_DIR=/data COMFYUI_URL=http://host.docker.internal:8188
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
