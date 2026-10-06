"""Server-side incident inspector. Read-only; no warehouse operation is retried.

docker compose -f docker-compose.prod.yml exec -T backend python -m app.inspect_incidents
Add --incident-id UUID to inspect evidence and delivery attempts, or --document-id UUID.
"""
import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select

from app.api.support import incident_card
from app.db.models import Incident, MonitoringDelivery
from app.db.session import AsyncSessionLocal


async def inspect(args):
    async with AsyncSessionLocal() as db:
        query = select(Incident)
        if args.incident_id:
            query = query.where(Incident.id == args.incident_id)
        if args.document_id:
            query = query.where(Incident.document_id == args.document_id)
        if args.status:
            query = query.where(Incident.status == args.status)
        rows = (await db.execute(query.order_by(Incident.last_seen_at.desc()).limit(args.limit))).scalars().all()
        cards = []
        for row in rows:
            card = incident_card(row, details=bool(args.incident_id))
            if args.incident_id:
                deliveries = (await db.execute(select(MonitoringDelivery).where(
                    MonitoringDelivery.incident_id == row.id).order_by(MonitoringDelivery.created_at))).scalars().all()
                card['deliveries'] = [{key: getattr(item, key) for key in
                    ('id', 'attempts', 'next_attempt_at', 'delivered_at', 'last_error')} for item in deliveries]
            cards.append(card)
        print(json.dumps(cards, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Просмотр инцидентов приёмки и отправки в МойСклад')
    parser.add_argument('--incident-id', type=UUID)
    parser.add_argument('--document-id', type=UUID)
    parser.add_argument('--status', choices=['open', 'resolved'])
    parser.add_argument('--limit', type=int, default=30)
    asyncio.run(inspect(parser.parse_args()))
