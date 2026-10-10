#!/usr/bin/env bash
set -euo pipefail

rule=(INPUT -p tcp --dport 80 -m comment --comment btck-acme-temporary -j ACCEPT)
if /usr/sbin/iptables -C "${rule[@]}" 2>/dev/null; then
  /usr/sbin/iptables -D "${rule[@]}"
fi
