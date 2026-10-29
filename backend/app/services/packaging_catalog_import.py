"""Seed public packaging facts from saved successful CZ responses, without API calls.

Run once after migrating: python -m app.services.packaging_catalog_import --apply
Repeated imports are classification-idempotent; observations count responses read,
not distinct physical marks. No raw codes/owners are written to the catalogue.
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import CzLog
from app.db.session import AsyncSessionLocal
from app.services.chestnyznak import extract_gtin, normalize_gtin_key
from app.services.packaging_catalog import observations_from_facts, record_observations


def facts_from_log(request, response):
    if not isinstance(request, list) or not isinstance(response, list):
        return []
    requested_gtins = {normalize_gtin_key(extract_gtin(code)) for code in request if isinstance(code, str)}
    facts = []
    for entry in response:
        if not isinstance(entry, dict) or entry.get('errorCode'):
            continue
        ci = entry.get('cisInfo')
        if not isinstance(ci, dict) or ci.get('errorCode'):
            continue
        gtin = normalize_gtin_key(ci.get('gtin'))
        if not gtin or gtin not in requested_gtins:
            continue
        facts.append((gtin, gtin, ci.get('generalPackageType') or ci.get('packageType'), bool(ci.get('child'))))
    return facts


async def import_history(hours=24, limit=10000, apply=False):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    cursor, scanned, totals = None, 0, {}
    while scanned < limit:
        query = select(CzLog.id, CzLog.request_body, CzLog.response_body).where(
            CzLog.created_at >= since, CzLog.response_status == 200,
            CzLog.request_url.contains('/cises/info')).order_by(CzLog.id).limit(min(100, limit - scanned))
        if cursor is not None:
            query = query.where(CzLog.id > cursor)
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(query)).all()
        if not rows:
            break
        facts = [fact for row in rows for fact in facts_from_log(row.request_body, row.response_body)]
        for gtin, (mask, count) in observations_from_facts(facts).items():
            old_mask, old_count = totals.get(gtin, (0, 0))
            totals[gtin] = (old_mask | mask, old_count + count)
        if apply:
            if not await record_observations(facts, source='cz_history'):
                raise RuntimeError('Не удалось сохранить сведения об упаковках из журнала ЧЗ')
        scanned += len(rows)
        cursor = rows[-1].id
    return {'applied': apply, 'logs_read': scanned, 'gtins': len(totals),
            'unit_gtins': sum(mask == 1 for mask, _ in totals.values()),
            'aggregate_gtins': sum(mask in (2, 4) for mask, _ in totals.values()),
            'conflicted_gtins': sum(mask not in (1, 2, 4) for mask, _ in totals.values()),
            'limit_reached': scanned == limit}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Начальное заполнение справочника упаковок из журнала ЧЗ')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--hours', type=int, default=24)
    parser.add_argument('--limit', type=int, default=10000)
    args = parser.parse_args()
    if args.hours < 1 or args.limit < 1:
        parser.error('Укажите положительные часы и лимит записей')
    print(json.dumps(asyncio.run(import_history(args.hours, args.limit, args.apply)), ensure_ascii=False))
