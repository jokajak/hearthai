FROM python:3.11-slim

# The store shells out to git for versioning, so git is a runtime dependency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin hearth

WORKDIR /app
COPY hearthmem/ ./hearthmem/

ENV HEARTHMEM_ROOT=/data \
    HEARTHMEM_HOST=0.0.0.0 \
    HEARTHMEM_PORT=8765 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# git refuses to operate on a repo owned by another uid; the volume is ours.
RUN git config --system --add safe.directory /data \
 && mkdir -p /data && chown hearth:hearth /data

USER hearth
EXPOSE 8765
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2)"

ENTRYPOINT ["python3", "-m", "hearthmem.server"]
