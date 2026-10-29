"""Catalog-wide GTIN comparison; never mutates MS products or saved mappings."""
from collections import Counter, defaultdict
from urllib.parse import urlsplit
from uuid import UUID

from app.services.gtin_card import product_gtins

ACTIVE = {'queued', 'running'}
CATEGORIES = {
    'other_product', 'invalid_product_id', 'missing_product', 'ambiguous_mapping',
    'archived_product', 'unconfirmed_gtin', 'renamed_product',
    'duplicate_active', 'duplicate_with_archive', 'duplicate_archived',
}


def safe_card_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    return value if (parsed.scheme == 'https' and parsed.netloc == 'online.moysklad.ru') else None


class Catalog:
    def __init__(self):
        self.cards = {}
        self.by_gtin = defaultdict(set)
        self.counts = Counter()

    def add(self, row):
        pid = row.get('id')
        if not pid or pid in self.cards:
            raise ValueError('Каталог МС изменился во время выгрузки. Запустите проверку повторно.')
        regular = set(product_gtins(row))
        packs = {key for pack in row.get('packs') or [] for key in product_gtins(pack)}
        gtins = sorted(regular | packs)
        self.cards[pid] = {'id': pid, 'name': row.get('name') or 'Без названия',
            'archived': bool(row.get('archived')), 'gtins': gtins, 'pack_gtins': sorted(packs),
            'url': safe_card_url((row.get('meta') or {}).get('uuidHref'))}
        self.counts['archived_products' if row.get('archived') else 'active_products'] += 1
        for gtin in gtins:
            self.by_gtin[gtin].add(pid)

    def candidates(self, gtin):
        return sorted((self.cards[pid] for pid in self.by_gtin.get(gtin, [])),
                      key=lambda card: (card['archived'], card['name'], card['id']))


def finding(kind, category, priority, gtin, candidates, mapping=None, saved_card=None):
    mapping = mapping or {}
    return {'kind': kind, 'category': category, 'priority': priority, 'gtin': gtin,
        'saved_product_id': mapping.get('product_id'), 'saved_product_name': mapping.get('product_name'),
        'saved_card': saved_card, 'candidates': candidates,
        'search_text': ' '.join([gtin, mapping.get('product_id') or '', mapping.get('product_name') or '',
            *[c['name'] for c in candidates], *[c['id'] for c in candidates],
            (saved_card or {}).get('name') or '']).casefold()}


def analyze(mappings, catalog):
    results, counts = [], Counter()
    for mapping in mappings:
        gtin, pid = mapping['gtin'], mapping['product_id']
        saved = catalog.cards.get(pid)
        candidates = catalog.candidates(gtin)
        active = [card for card in candidates if not card['archived']]
        matches = saved and gtin in saved['gtins']
        if matches and not saved['archived']:
            if len(active) > 1:
                category, priority = 'ambiguous_mapping', 5
            elif (mapping.get('product_name') or '').strip() != saved['name'].strip():
                category, priority = 'renamed_product', 8
            else:
                counts['matched'] += 1
                continue
        elif not saved:
            try:
                UUID(pid)
                category, priority = 'missing_product', 3
            except (ValueError, TypeError, AttributeError):
                category, priority = 'invalid_product_id', 1
        elif matches:
            category, priority = 'archived_product', 6
        elif active:
            category, priority = ('other_product', 2) if len(active) == 1 else ('ambiguous_mapping', 4)
        else:
            category, priority = 'unconfirmed_gtin', 7
        counts[category] += 1
        results.append(finding('mapping', category, priority, gtin, candidates, mapping, saved))
    for gtin, ids in catalog.by_gtin.items():
        if len(ids) < 2:
            continue
        cards = catalog.candidates(gtin)
        active_count = sum(not card['archived'] for card in cards)
        category = ('duplicate_active' if active_count > 1 else
                    'duplicate_with_archive' if active_count else 'duplicate_archived')
        counts[category] += 1
        results.append(finding('duplicate', category, 1 if active_count > 1 else 2, gtin, cards))
    summary = {**dict(catalog.counts), **dict(counts), 'mapping_count': len(mappings),
        'mapping_findings': sum(row['kind'] == 'mapping' for row in results),
        'duplicate_findings': sum(row['kind'] == 'duplicate' for row in results),
        'catalog_gtins': len(catalog.by_gtin)}
    return results, summary
