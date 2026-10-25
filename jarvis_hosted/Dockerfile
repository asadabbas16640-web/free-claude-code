FROM python:3.14.7-slim
WORKDIR /opt/fcc
COPY . .
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
RUN pip install --no-cache-dir .
ENV FCC_OPEN_BROWSER=false MESSAGING_PLATFORM=none VOICE_NOTE_ENABLED=false PROXY_AUTH_ENABLED=true MODEL_FALLBACKS="" LOG_RAW_API_PAYLOADS=false LOG_RAW_SSE_EVENTS=false LOG_API_ERROR_TRACEBACKS=false
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn jarvis_hosted.gateway:create_gateway --factory --host 0.0.0.0 --port ${PORT:-10000} --no-access-log"]
