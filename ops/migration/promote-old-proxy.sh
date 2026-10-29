#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == spb-3-vm-enap ]] || exit 2
for name in ms_scanner-backend-1 ms_scanner-worker-1 ms-sverka ms-order-import; do
  [[ $(docker inspect --format '{{.State.Running}}' "$name") == false ]] || exit 2
done
for endpoint in skandata.ru/health sverka.skandata.ru/healthz otgruzka.skandata.ru/healthz; do
  domain=${endpoint%%/*}
  curl --noproxy '*' --fail --silent --show-error --max-time 15 --resolve "$domain:443:37.18.102.53" "https://$endpoint" >/dev/null
done
bridge=/root/ms_scanner_migration/Caddyfile.bridge.active
sed 's/NEW_SERVER_IP/37.18.102.53/g' /root/ms_scanner_migration/Caddyfile.bridge.example > "$bridge"
docker exec -i ms_scanner-caddy-1 caddy adapt --config /dev/stdin --adapter caddyfile < "$bridge" >/dev/null
cat "$bridge" > /root/ms_scanner/Caddyfile
docker exec ms_scanner-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
docker update --restart=no ms_scanner-backend-1 ms_scanner-worker-1 ms-sverka ms-order-import
printf '%s\n' 'Production moved to skand@37.18.102.53 on 2026-10-29.' 'This host only proxies HTTPS. Do not restart old application containers.' > /root/PRODUCTION_MOVED.txt
date -u +%FT%TZ > /root/ms_scanner_migration/final-20261029/proxy-enabled.utc
echo 'OLD_SERVER_PROXY_ENABLED'
