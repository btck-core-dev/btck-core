FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY src/ /app/src/
COPY sdk/ /app/sdk/
COPY web/ /app/web/
COPY scripts/ /app/scripts/

# Install btck-agent SDK
RUN pip install --no-cache-dir -e /app/sdk

EXPOSE 16110 16112 3333 8080

CMD ["python3", "/app/src/node/daemon.py"]
