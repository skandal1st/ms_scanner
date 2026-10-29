from types import SimpleNamespace as NS
from uuid import uuid4
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from app.db.models import DocumentStatus, ScanStatus
from app.services.scan_packaging import needs_packaging_discovery
from app.services import scan_packaging_discovery as service
from tests.test_release_processing import Result


def test_unknown_block_gtin_resolves_to_ten_unit_codes_without_verifying():
    children = [f'010463033960870521SERIAL{i}' for i in range(10)]
    metadata = service.packaging_metadata(NS(package_type='GROUP', children=children,
                                            gtin='04630339608897'), '04630339608897')
    assert metadata['gtin'] == '04630339608705'
    assert metadata['box_quantity'] == 10
    assert metadata['child_codes'] == children
    assert metadata['keep_aggregate']
    assert 'verified_at' not in metadata and 'verification' not in metadata


@pytest.mark.parametrize('children', [[], ['not-a-mark'], ['010463033960870521A', '010467016300240921B']])
def test_incomplete_or_mixed_pack_is_not_counted_as_one_unit(children):
    with pytest.raises(ValueError):
        service.packaging_metadata(NS(package_type='GROUP', children=children), '04630339608897')


def test_discovery_never_infers_unit_packaging_from_product_matching():
    plan = [{'product_id': 'p', 'gtin': '04630339608705', 'pack_gtins': ['04630339608897']}]
    assert needs_packaging_discovery(plan, '04630339608897', 'p', None)
    assert needs_packaging_discovery(plan, '04630339608897', None, None)
    assert needs_packaging_discovery([], '04630339608897', None, None)
    assert needs_packaging_discovery([], '04610113991414', 'remembered-muassel', None)
    assert not needs_packaging_discovery([], '04610113991414', 'remembered-muassel', 10)
    assert needs_packaging_discovery(plan, '04630339608705', 'p', None)
    assert not needs_packaging_discovery(plan, '04630339608897', 'p', 10)


def test_deus_block_stored_as_regular_product_barcode_still_gets_discovered():
    plan = [{'gtin': '02000000096872', 'gtins': ['02000000096872', '04610684000706',
             '14610684000703', '04640195516373'], 'product_id': 'tokyo-spring',
             'pack_gtins': [], 'pack_quantities': {}}]
    assert needs_packaging_discovery(plan, '04610684000706', 'tokyo-spring', None)
    children = [f'010464019551637321PACK{i}' for i in range(10)]
    metadata = service.packaging_metadata(NS(package_type='GROUP', children=children), '04610684000706')
    assert metadata['gtin'] == '04640195516373'
    assert metadata['box_quantity'] == 10 and metadata['package_type'] == 'GROUP'


def test_actual_unit_from_cz_keeps_single_unit_semantics():
    metadata = service.packaging_metadata(NS(package_type='UNIT', children=[],
                                            gtin='04640195516373'), '04640195516373')
    assert metadata == {'gtin': '04640195516373', 'package_type': 'UNIT',
                        'box_quantity': None, 'child_codes': None, 'keep_aggregate': False}


@pytest.mark.parametrize('state', ['ready', 'removed', 'finished', 'verified'])
async def test_discovery_preserves_raw_code_and_requires_later_verification(monkeypatch, state):
    from app.worker import tasks
    scan = NS(id=uuid4(), document_id=uuid4(), status=ScanStatus.pending,
              verification={'source': 'packaging_pending'}, code='010463033960889721BLOCK',
              gtin='04630339608897', moysklad_product_id=None, product_name=None)
    doc = NS(id=scan.document_id, user_id=uuid4(), status=DocumentStatus.draft,
             plan=[{'product_id': 'product', 'gtin': '04630339608705', 'product_name': 'Dzen Roll'}])
    children = [f'010463033960870521SERIAL{i}' for i in range(10)]
    db = NS(get=AsyncMock(side_effect=[scan, doc]), rollback=AsyncMock(), commit=AsyncMock(),
            execute=AsyncMock(side_effect=[Result(doc), Result(None if state == 'removed' else scan)]))
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(service, 'AsyncSessionLocal', session)
    monkeypatch.setattr(tasks, '_get_cz_token', AsyncMock(return_value='fake'))
    monkeypatch.setattr(tasks, '_get_cz_product_groups', AsyncMock(return_value=['otp']))
    async def get_info(code):
        if state == 'finished':
            doc.status = DocumentStatus.accepted
        elif state == 'verified':
            scan.status = ScanStatus.valid
            scan.verification = {'source': 'cz_info'}
        return NS(package_type='GROUP', children=children, gtin='04630339608897')
    monkeypatch.setattr(service, 'ChestnyZnakService', lambda **kwargs: NS(get_code_info=get_info))
    monkeypatch.setattr(service, 'publish_scan', AsyncMock())
    await service.discover_scan_packaging(str(scan.id), str(doc.user_id))
    if state != 'ready':
        db.commit.assert_not_awaited()
        return
    assert scan.status == ScanStatus.scanned and scan.verification is None
    assert scan.box_quantity == 10 and scan.package_type == 'GROUP'
    assert scan.gtin == '04630339608705' and scan.moysklad_product_id == 'product'
    assert scan.code == '010463033960889721BLOCK' and scan.keep_aggregate
    service.publish_scan.assert_awaited_once()


@pytest.mark.parametrize('queue_failed', [False, True])
async def test_unknown_gtin_schedules_discovery_and_queue_failure_remains_retryable(monkeypatch, queue_failed):
    from app.api import scans
    from app.worker import tasks
    doc = NS(kind='demand', plan=[])
    monkeypatch.setattr(scans, 'editable_document', AsyncMock(return_value=doc))
    monkeypatch.setattr(scans, 'get_cached_pg', AsyncMock(return_value=None))
    monkeypatch.setattr(scans, 'get_gtin_product', AsyncMock(return_value=None))
    delay = Mock(side_effect=RuntimeError('broker unavailable') if queue_failed else None)
    monkeypatch.setattr(tasks.resolve_scan_packaging_task, 'delay', delay)
    db = NS(execute=AsyncMock(side_effect=[Result(None), Result(None)]), add=Mock(),
            commit=AsyncMock(), refresh=AsyncMock())
    scan, duplicate = await scans._create_scan_record(db, uuid4(),
        '010463033960889721BLOCK0000001\x1d93TEST', uuid4())
    assert not duplicate
    delay.assert_called_once()
    if queue_failed:
        assert scan.status == ScanStatus.scanned and scan.verification is None
        assert 'Проверить марки' in scan.error_message
    else:
        assert scan.status == ScanStatus.pending
        assert scan.verification['source'] == 'packaging_pending'


@pytest.mark.parametrize('confirmed', [False, True])
async def test_known_product_skips_packaging_wait_only_with_confirmed_unit_evidence(monkeypatch, confirmed):
    from app.api import scans
    from app.worker import tasks
    from app.services import scan_packaging_cache
    user_id, doc_id = uuid4(), uuid4()
    doc = NS(kind='demand', plan=[{'product_id': 'product', 'product_name': 'Test product',
                                 'gtin': '04630339608897'}])
    monkeypatch.setattr(scans, 'editable_document', AsyncMock(return_value=doc))
    monkeypatch.setattr(scans, 'get_cached_pg', AsyncMock(return_value=None))
    monkeypatch.setattr(scan_packaging_cache, 'has_confirmed_unit', AsyncMock(return_value=confirmed))
    db = NS(execute=AsyncMock(return_value=Result(None)), add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    scan, duplicate = await scans._create_scan_record(db, doc_id,
        '010463033960889721UNIT00000001\x1d93TEST', user_id)
    assert not duplicate
    scan_packaging_cache.has_confirmed_unit.assert_awaited_once_with(user_id, doc_id, '04630339608897', db=db)
    assert scan.moysklad_product_id == 'product'
    assert scan.verified_at is None  # Packaging evidence never certifies the mark.
    if confirmed:
        assert scan.status == ScanStatus.scanned and scan.verification is None
        tasks.resolve_scan_packaging_task.delay.assert_not_called()
    else:
        assert scan.status == ScanStatus.pending
        tasks.resolve_scan_packaging_task.delay.assert_called_once()
