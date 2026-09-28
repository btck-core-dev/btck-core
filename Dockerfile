FROM python:3.12-slim

WORKDIR /app

# Copy project files
COPY --chown=10001:10001 src/ /app/src/
COPY --chown=10001:10001 sdk/ /app/sdk/
COPY --chown=10001:10001 web/ /app/web/
COPY --chown=10001:10001 scripts/ /app/scripts/
COPY --chown=10001:10001 params/ /app/params/

# Install btck-agent SDK
RUN pip install --no-cache-dir -e /app/sdk

RUN groupadd --gid 10001 btck && useradd --uid 10001 --gid 10001 --no-create-home btck \
    && mkdir -p /app/data && chown -R 10001:10001 /app/data /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001

EXPOSE 16110 16112 3333

CMD ["python3", "/app/src/node/daemon.py"]
