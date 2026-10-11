#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_ROOT="$PROJECT_ROOT/backups/mainnet-guardian"
SSH_KEY="${BTCK_SSH_KEY:-$HOME/.ssh/btck_deploy_ed25519}"
SSH_PROXY="${BTCK_SSH_PROXY:-127.0.0.1:9675}"
REMOTE="ubuntu@185.130.45.240"
REMOTE_ROOT="/home/ubuntu/btck-mainnet-backups"
KEEP_LOCAL=14

mkdir -p "$BACKUP_ROOT"
ssh_options=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15)
if [[ -n "$SSH_PROXY" ]]; then
  ssh_options+=(-o "ProxyCommand=nc -x $SSH_PROXY -X 5 %h %p")
fi

latest_name="$(ssh "${ssh_options[@]}" "$REMOTE" \
  "basename \"\$(readlink -f '$REMOTE_ROOT/latest')\"")"

if [[ ! "$latest_name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Refusing unexpected remote backup name: $latest_name" >&2
  exit 1
fi

destination="$BACKUP_ROOT/$latest_name"
if [[ ! -d "$destination" ]]; then
  temporary="$(mktemp -d "$BACKUP_ROOT/.pull.XXXXXX")"
  cleanup() { rm -rf -- "$temporary"; }
  trap cleanup EXIT
  rsync_ssh="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15"
  if [[ -n "$SSH_PROXY" ]]; then
    rsync_ssh+=" -o ProxyCommand='nc -x $SSH_PROXY -X 5 %h %p'"
  fi
  rsync -a --partial -e "$rsync_ssh" \
    "$REMOTE:$REMOTE_ROOT/$latest_name/" "$temporary/"
  (cd "$temporary" && shasum -a 256 -c SHA256SUMS)
  mv "$temporary" "$destination"
  trap - EXIT
  echo "Verified guardian backup: $destination"
else
  echo "Guardian backup already present: $destination"
fi

shopt -s nullglob
snapshots=("$BACKUP_ROOT"/20??????T??????Z)
if ((${#snapshots[@]} > KEEP_LOCAL)); then
  while IFS= read -r expired; do
    base="${expired##*/}"
    if [[ "$expired" == "$BACKUP_ROOT/"* && "$base" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
      rm -rf -- "$expired"
      echo "Pruned local backup: $base"
    fi
  done < <(printf '%s\n' "${snapshots[@]}" | sort -r | tail -n "+$((KEEP_LOCAL + 1))")
fi
