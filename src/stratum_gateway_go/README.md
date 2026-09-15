# BTC-K High-Concurrency Go-Stratum + Redis Enterprise Gateway

## Overview
High-throughput, event-driven Stratum V1 mining pool gateway designed for global multi-thousand ASIC deployment.
Fully compatible with:
- **IceRiver**: KS0, KS0 Pro, KS0 Ultra, KS1, KS2, KS3, KS5, KS5 Pro
- **Bitmain Antminer**: KS3, KS5

## Key Features
1. **Goroutine-per-Connection Architecture**: Capable of sustaining 50,000+ simultaneous ASIC TCP connections per node with sub-millisecond dispatch latency.
2. **IceRiver 82-Character Packet Engine**: Strict compliance with IceRiver ASIC hardware requirement:
   `[HeaderHash(64 hex)] + [TimestampMs(16 hex)] + [CleanJobs(2 hex)]` = Exactly 82 characters.
3. **Redis Stream / Pub-Sub Message Bus**:
   - Subscribes to `btck:jobs:broadcast` for 1ms node-to-gateway block notifications.
   - Publishes shares to `btck:shares:stream` for asynchronous accounting, preventing TCP pipeline stalls.
   - Fallback mode: Seamlessly falls back to in-memory channel bus when Redis is offline.
4. **Vardiff Auto-Adjustment Engine**: Sliding window retargeting to anchor block/share rate to 1.0s.

## Quick Start

### Standalone Run
```bash
export STRATUM_PORT=3334
export REDIS_ADDR=127.0.0.1:6379
go run .
```

### Docker Compose Cluster
```bash
docker compose -f deploy/docker-compose.pool-cluster.yml up -d
```
