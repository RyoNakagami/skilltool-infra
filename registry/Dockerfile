# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps kept minimal; wheels cover everything.
RUN pip install --no-cache-dir \
        "fastapi==0.115.*" \
        "uvicorn[standard]==0.32.*" \
        "python-multipart==0.0.12" \
        "pyyaml==6.0.*"

COPY server.py ./

ENV SKILLTOOL_STORAGE_DIR=/data
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health').status==200 else 1)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765"]
