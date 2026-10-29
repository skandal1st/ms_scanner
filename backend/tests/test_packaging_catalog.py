from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import GtinPackaging
from app.services.packaging_catalog import observations_from_facts, observation_upsert, is_confirmed_unit
from app.services.scan_packaging_cache import has_confirmed_unit

GTIN = '04640195516373'


def test_only_explicit_matched_cz_types_are_learned():
    assert observations_from_facts([
        (GTIN, GTIN, 'UNIT', False),
        (GTIN, GTIN, None, False),
        (GTIN, '04630339608897', 'UNIT', False),
        (None, GTIN, 'UNIT', False),
    ]) == {GTIN: (1, 1)}
    assert observations_from_facts([(GTIN, GTIN, 'LEVEL1', True)]) == {GTIN: (2, 1)}
    assert observations_from_facts([(GTIN, GTIN, 'LEVEL2', True)]) == {GTIN: (4, 1)}


def test_historical_import_ignores_unmatched_failed_and_unknown_responses():
    from app.services.packaging_catalog_import import facts_from_log
    request=[f'01{GTIN}21TEST']
    response=[{'cisInfo':{'gtin':GTIN,'generalPackageType':'UNIT','child':[], 'ownerInn':'private'}}]
    assert facts_from_log(request,response)==[(GTIN,GTIN,'UNIT',False)]
    assert facts_from_log(['010463033960889721OTHER'],response)==[]
    assert facts_from_log({'count':1},response)==[]
    assert facts_from_log(request,[{'errorCode':'404','cisInfo':response[0]['cisInfo']}])==[]


def test_conflicts_are_preserved_inside_one_batch():
    assert observations_from_facts([(GTIN, GTIN, 'UNIT', False),
        (GTIN, GTIN, 'GROUP', True), (GTIN, GTIN, 'UNIT', False)]) == {GTIN: (3, 3)}
    assert observations_from_facts([(GTIN, GTIN, 'UNIT', True)]) == {GTIN: (3, 1)}


@pytest.mark.parametrize('mask,expected', [(None,False),(1,True),(2,False),(3,False),(4,False),(5,False),(7,False)])
async def test_only_unambiguous_unit_can_skip_discovery(mask, expected):
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: mask)))
    assert await is_confirmed_unit(db, GTIN) == expected
    query = db.execute.await_args.args[0]
    assert GTIN in query.compile().params.values()


async def test_lookup_is_shared_across_clients_and_documents_without_redis():
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: 1)))
    assert await has_confirmed_unit('client-a', 'document-a', GTIN, db=db)
    assert await has_confirmed_unit('client-b', 'document-b', GTIN, db=db)
    assert db.execute.await_args_list[0].args[0].compile().params == db.execute.await_args_list[1].args[0].compile().params


def test_atomic_upsert_and_schema_contain_only_public_packaging_facts():
    stmt = observation_upsert([(GTIN, GTIN, 'UNIT', False)], 'cz_info')
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert 'ON CONFLICT' in sql and ' | ' in sql
    assert not {'user_id','document_id','code','children','owner_inn','product_id'} & set(GtinPackaging.__table__.columns.keys())
    with pytest.raises(ValueError):
        observation_upsert([(GTIN,GTIN,'UNIT',False)], 'ms')


async def test_ms_pack_quantity_never_overrides_confirmed_unit(monkeypatch):
    from app.api import scans
    from app.worker import tasks
    from app.services import scan_packaging_cache
    from tests.test_release_processing import Result
    from unittest.mock import Mock
    from uuid import uuid4
    doc = NS(kind='demand', plan=[{'product_id':'deus','product_name':'DEUS',
        'pack_gtins':[GTIN], 'pack_quantities':{GTIN:20}}])
    monkeypatch.setattr(scans, 'editable_document', AsyncMock(return_value=doc))
    monkeypatch.setattr(scans, 'get_cached_pg', AsyncMock(return_value=None))
    monkeypatch.setattr(scan_packaging_cache, 'has_confirmed_unit', AsyncMock(return_value=True))
    db = NS(execute=AsyncMock(return_value=Result(None)), add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    scan, _ = await scans._create_scan_record(db, uuid4(), f'01{GTIN}21UNIT00000001\x1d93TEST', uuid4())
    assert scan.status.value == 'scanned' and scan.box_quantity is None
    assert scan.package_type == 'UNIT' and not scan.keep_aggregate
    tasks.resolve_scan_packaging_task.delay.assert_not_called()
