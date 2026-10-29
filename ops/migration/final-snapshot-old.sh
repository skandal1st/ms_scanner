#!/usr/bin/env bash
# Approved cutover window only: fences writes, stops old apps, captures final data.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == spb-3-vm-enap ]] || exit 2
stage=/root/ms_scanner_migration/final-20261029
[[ ! -e $stage ]] || { echo 'Final snapshot already exists; inspect before retrying.' >&2; exit 2; }
umask 077
mkdir -m 0700 "$stage"
cp -p /root/ms_scanner/Caddyfile "$stage/Caddyfile.production.before"
docker exec -i ms_scanner-caddy-1 caddy adapt --config /dev/stdin --adapter caddyfile < /root/ms_scanner_migration/Caddyfile.maintenance >/dev/null
cat /root/ms_scanner_migration/Caddyfile.maintenance > /root/ms_scanner/Caddyfile
docker exec ms_scanner-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
docker stop --time 120 ms_scanner-worker-1 ms_scanner-backend-1 ms-sverka ms-order-import
date -u +%FT%TZ > "$stage/writers-stopped.utc"
docker exec ms_scanner-postgres-1 sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$stage/postgres.dump"
docker exec -i ms_scanner-postgres-1 sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' > "$stage/postgres-counts.json" <<'SQL'
SELECT json_build_object('users',(SELECT count(*) FROM users),'integrations',(SELECT count(*) FROM integrations),'documents',(SELECT count(*) FROM documents),'scans',(SELECT count(*) FROM scans),'tsd_devices',(SELECT count(*) FROM tsd_devices),'gtin_packaging',(SELECT count(*) FROM gtin_packaging),'edo_documents',(SELECT count(*) FROM edo_documents),'edo_marks',(SELECT count(*) FROM edo_marks));
SQL
docker exec ms_scanner-redis-1 redis-cli SAVE
docker cp ms_scanner-redis-1:/data/dump.rdb "$stage/redis.rdb"
for volume in ms_sverka_vendor_data ms_zakaz_order_data ms_scanner_caddy_data ms_scanner_caddy_config; do
  mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
  tar --numeric-owner -C "$mountpoint" -czf "$stage/$volume.tgz" .
done
python3 - "$stage" <<'PY'
import json,pathlib,sqlite3,subprocess,sys
stage=pathlib.Path(sys.argv[1])
result={}
for volume,filename in [('ms_sverka_vendor_data','vendor.sqlite3'),('ms_zakaz_order_data','state.sqlite')]:
    mountpoint=subprocess.check_output(['docker','volume','inspect','--format','{{.Mountpoint}}',volume],text=True).strip()
    con=sqlite3.connect('file:'+str(pathlib.Path(mountpoint)/filename)+'?mode=ro',uri=True)
    integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity!='ok':raise RuntimeError('Source SQLite integrity failed: '+volume)
    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    result[volume]={'file':filename,'integrity':integrity,'counts':{t:con.execute('SELECT count(*) FROM "'+t.replace('"','""')+'"').fetchone()[0] for t in tables}}
    con.close()
(stage/'sqlite-counts.json').write_text(json.dumps(result))
PY
test -s "$stage/postgres.dump"
test -s "$stage/redis.rdb"
(cd "$stage" && sha256sum postgres.dump redis.rdb *.tgz postgres-counts.json sqlite-counts.json > SHA256SUMS)
date -u +%FT%TZ > "$stage/snapshot-complete.utc"
echo 'FINAL_SNAPSHOT_READY'
