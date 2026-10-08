# BTC-K public HTTPS edge

This overlay is deliberately opt-in. Do not enable it during a fixed
acceptance window. It exposes only the read-only Explorer through Nginx;
node RPC remains host-loopback and Stratum/P2P remain separate TCP services.

Prerequisites:

1. Point the chosen hostname to the server. Until the purchased domain is
   known, keep the reserved placeholders in `.env.public.example`; never issue
   a certificate for or deploy those placeholder names.
2. Obtain a certificate outside this Compose project and keep the private key
   root-readable only. Never copy it into the repository.
3. Copy `.env.public.example` to an external permission-restricted env file
   and set the real hostname and absolute certificate paths.
4. Run `python3 deploy/public-edge/validate_public_edge.py --env-file PATH`.

After the RC window and an approved maintenance plan, render before starting:

```bash
docker compose \
  --env-file /secure/path/btck-public-edge.env \
  -f docker-compose.yml \
  -f deploy/public-edge/docker-compose.public-edge.yml \
  --profile public-edge config
```

Then start only after reviewing the rendered mounts, published ports and host.
Container stdout/stderr contains JSON access logs and is bounded by Docker's
`json-file` rotation (`25m`, ten files, compression enabled).

The current read-only Explorer also displays pool statistics. Both
`BTCK_PUBLIC_HOST` and `BTCK_POOL_PUBLIC_HOST` are active aliases to that same
read-only service; the production certificate must cover both names. This lets
the purchased domains replace only DNS, the two host values and TLS files.

The image tag is version-pinned but not digest-pinned because manifests differ
by architecture. Deployment approval must record `docker image inspect`'s
RepoDigest in the release evidence.
