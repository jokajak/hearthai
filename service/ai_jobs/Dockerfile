# One image for the control plane (`ai-jobs serve`) and every sandbox worker
# (`ai-jobs-worker <profile>`), spec E7. The two share code but not authority: a
# sandbox pod runs the worker command from its profile, with no service-account
# token and no ambient credentials, so the control plane's code being present
# in the image grants the worker nothing.
#
# Build context is this directory (service/ai_jobs).

FROM python:3.13-slim AS build

WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
# A venv, not --user or a system install, so the final stage copies one directory
# and nothing from the build stage (pip caches, setuptools build trees) travels.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.13-slim

COPY --from=build /opt/venv /opt/venv

# Read-only root compatible: nothing is written outside a mounted volume. Bytecode is
# not written (the root is read-only, and the package is compiled at build time by pip),
# and HOME points at the emptyDir the sandbox profile mounts at /tmp.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

# The uid/gid the sandbox pod spec fixes (ai_jobs.sandbox.RUN_AS). A numeric user
# needs no /etc/passwd entry, and runAsNonRoot can verify a number.
USER 65532:65532
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD ["python3", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2)"]

# Sandbox profiles override the command; the control plane's pod uses this.
ENTRYPOINT ["ai-jobs"]
CMD ["serve"]
