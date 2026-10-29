#!/usr/bin/env bash
# One-time restore of the fenced source snapshot. Never overwrites existing data.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == skandata ]] || exit 2
stage=/var/lib/skandata-migration/final-20261029
helpers=/home/skand/skandata-migration
[[ ! -e /var/lib/skandata-migration/restore-started ]] || { echo 'Inspect previous restore before retrying.' >&2; exit 2; }
cd "$stage"
sha256sum -c SHA256SUMS
[[ -z $(docker ps -aq) ]] || { echo 'Expected an empty destination Docker host.' >&2; exit 2; }
date -u +%FT%TZ > /var/lib/skandata-migration/restore-started
for volume in ms_sverka_vendor_data ms_zakaz_order_data ms_scanner_caddy_data ms_scanner_caddy_config; do
  docker volume create "$volume" >/dev/null
  mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
  [[ -z $(find "$mountpoint" -mindepth 1 -print -quit) ]] || exit 2
  tar --numeric-owner -C "$mountpoint" -xzf "$stage/$volume.tgz"
done
docker volume create ms_scanner_redis_data >/dev/null
mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' ms_scanner_redis_data)
[[ -z $(find "$mountpoint" -mindepth 1 -print -quit) ]] || exit 2
cp "$stage/redis.rdb" "$mountpoint/dump.rdb"
docker run --rm --entrypoint sh -v ms_scanner_redis_data:/data skandata-migration/redis:20261029 -c 'chown redis:redis /data/dump.rdb'
cp -p "$stage/Caddyfile.production.before" /root/ms_scanner/Caddyfile
scanner=(docker compose --env-file /root/ms_scanner/.env -p ms_scanner -f /root/ms_scanner/docker-compose.prod.yml -f "$helpers/compose.scanner.restore.yml")
"${scanner[@]}" up -d --no-build --pull never postgres redis
for attempt in {1..60}; do
  if docker exec ms_scanner-postgres-1 sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null; then break; fi
  sleep 1
done
tables=$(docker exec ms_scanner-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "SELECT count(*) FROM information_schema.tables WHERE table_schema= '\''public'\''"')
[[ $tables == 0 ]] || { echo 'Destination database is not empty.' >&2; exit 2; }
docker exec -i ms_scanner-postgres-1 sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl --exit-on-error' < "$stage/postgres.dump"
docker exec -i ms_scanner-postgres-1 sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' > "$stage/postgres-counts.restored.json" <<'SQL'
SELECT json_build_object('users',(SELECT count(*) FROM users),'integrations',(SELECT count(*) FROM integrations),'documents',(SELECT count(*) FROM documents),'scans',(SELECT count(*) FROM scans),'tsd_devices',(SELECT count(*) FROM tsd_devices),'gtin_packaging',(SELECT count(*) FROM gtin_packaging),'edo_documents',(SELECT count(*) FROM edo_documents),'edo_marks',(SELECT count(*) FROM edo_marks));
SQL
python3 - "$stage" <<'PY'
import json,pathlib,sqlite3,subprocess,sys
stage=pathlib.Path(sys.argv[1])
expected=json.loads((stage/'postgres-counts.json').read_text())
actual=json.loads((stage/'postgres-counts.restored.json').read_text())
assert actual==expected, 'PostgreSQL row counts differ'
for volume,expected in json.loads((stage/'sqlite-counts.json').read_text()).items():
    mountpoint=subprocess.check_output(['docker','volume','inspect','--format','{{.Mountpoint}}',volume],text=True).strip()
    con=sqlite3.connect('file:'+str(pathlib.Path(mountpoint)/expected['file'])+'?mode=ro',uri=True)
    assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok', volume
    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts={t:con.execute('SELECT count(*) FROM "'+t.replace('"','""')+'"').fetchone()[0] for t in tables}
    assert counts==expected['counts'], 'SQLite row counts differ: '+volume
    con.close()
print('PostgreSQL and both SQLite databases: integrity/counts verified.')
PY
"${scanner[@]}" up -d --no-build --pull never backend frontend
SKANDATA_DOCKER_NETWORK=ms_scanner_default docker compose --env-file /opt/ms_sverka/.env.vendor -p ms_sverka -f /opt/ms_sverka/docker-compose.pilot.yml -f "$helpers/compose.sverka.restore.yml" up -d --no-build --pull never
docker compose --env-file /opt/ms_zakaz/.env.vendor -p ms_zakaz -f /opt/ms_zakaz/docker-compose.yml -f /opt/ms_zakaz/docker-compose.vendor.yml -f "$helpers/compose.zakaz.restore.yml" up -d --no-build --pull never
"${scanner[@]}" up -d --no-build --pull never caddy worker
date -u +%FT%TZ > /var/lib/skandata-migration/restore-complete
echo 'NEW_SERVER_STARTED'
