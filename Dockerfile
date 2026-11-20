# One image: builds the frontend, then serves it and the API from the backend.
FROM node:22-trixie-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-trixie
WORKDIR /app
COPY backend/requirements.txt ./
# The server needs no GUI, so use the headless build of the same OpenCV: it needs no system
# libraries. (The optional PaddleOCR engine pins the GUI build, so it isn't in this image.)
RUN sed 's/^opencv-contrib-python==/opencv-contrib-python-headless==/' requirements.txt > requirements-server.txt \
    && pip install --no-cache-dir -r requirements-server.txt
COPY backend/app ./app
COPY --from=frontend /frontend/dist ./frontend-dist
RUN useradd --uid 1000 --create-home secretary \
    && mkdir -p /data /backups && chown secretary:secretary /data /backups
USER secretary
ENV SECRETARY_DB_PATH=/data/secretary.db \
    SECRETARY_FRONTEND_DIST=/app/frontend-dist \
    PYTHONUNBUFFERED=1
VOLUME ["/data", "/backups"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
# One worker: SQLite and the in-memory sign-in rate limiter assume a single process.
# Proxy headers are trusted because only Caddy can reach this port (it isn't published).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
