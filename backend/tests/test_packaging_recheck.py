from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from uuid import uuid4
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException
from app.db.models import DocumentStatus, ScanStatus
from app.services import packaging_recheck as service, packaging_formats as formats
from app.worker import tasks
from tests.test_release_processing import Result

GTIN = '00802084154583'
RAW = '020080208415458313260930102MSCS2111012213710'
CHILDREN = ['00802084154583VWCG>f;', '00802084154583vX3Fpi*']


def scan(**changes):
    values = dict(id=uuid4(), document_id=uuid4(), status=ScanStatus.invalid, verification=None,
        is_box=False, is_barcode=False, code=RAW, gtin='02008020841545', box_quantity=None,
        child_codes=None, keep_aggregate=False, box_expanded=False, verified_at=None,
        error_message='Неверная контрольная сумма GTIN', moysklad_product_id='old', product_name='Old')
    values.update(changes)
    return NS(**values)


@pytest.mark.parametrize('fail', [False, True])
async def test_button_queues_without_external_io_and_restores_state_on_queue_failure(monkeypatch, fail):
    item = scan(status=ScanStatus.valid, verification={'source':'cz_info','checked_at':'before'})
    db = NS(refresh=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(service, 'editable_document', AsyncMock())
    monkeypatch.setattr(service, 'publish_scan', AsyncMock())
    delay = Mock(side_effect=RuntimeError() if fail else None)
    monkeypatch.setattr(tasks.recheck_scan_packaging_task, 'delay', delay)
    monkeypatch.setattr(service, 'ChestnyZnakService', Mock(side_effect=AssertionError('external IO in API')))
    if fail:
        with pytest.raises(HTTPException) as err:
            await service.start(db,item,uuid4())
        assert err.value.status_code == 503
        assert item.status == ScanStatus.valid and item.verification['source'] == 'cz_info'
    else:
        await service.start(db,item,uuid4())
        assert item.status == ScanStatus.pending and item.verification['source']=='packaging_recheck'
        assert item.verification['previous_verification']['source']=='cz_info'
        delay.assert_called_once()


@pytest.mark.parametrize('change', [{'is_barcode':True}, {'status':ScanStatus.used_in_other_doc}, {'status':ScanStatus.pending}])
async def test_non_marks_conflicts_and_running_checks_are_rejected(monkeypatch, change):
    monkeypatch.setattr(service,'editable_document',AsyncMock())
    db=NS(refresh=AsyncMock(),commit=AsyncMock())
    with pytest.raises(HTTPException):
        await service.start(db,scan(**change),uuid4())
    db.commit.assert_not_awaited()


@pytest.mark.parametrize('state', ['success','unit','missing','unknown_type','mixed','conflict','deleted','finished','newer'])
async def test_worker_updates_only_current_scan_and_never_learns_failed_composition(monkeypatch,state):
    rid=str(uuid4())
    item=scan(status=ScanStatus.pending,verification={'source':'packaging_recheck','request_id':rid,
        'previous_status':'invalid','previous_verification':None})
    doc=NS(id=item.document_id,user_id=uuid4(),status=DocumentStatus.draft,kind='demand',
        plan=[{'gtin':GTIN,'product_id':'chief','product_name':'Afzal Chief 40 g'}])
    db=NS(get=AsyncMock(side_effect=[item,doc]),rollback=AsyncMock(),commit=AsyncMock(),
        execute=AsyncMock(side_effect=[Result(doc),Result(None if state=='deleted' else item),
                                      Result(uuid4() if state=='conflict' else None)]))
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(service,'AsyncSessionLocal',session)
    monkeypatch.setattr(tasks,'_get_cz_token',AsyncMock(return_value='fake'))
    monkeypatch.setattr(tasks,'_get_cz_product_groups',AsyncMock(return_value=['otp']))
    async def get_info(code):
        assert code==RAW
        if state=='finished': doc.status=DocumentStatus.accepted
        if state=='newer': item.verification={**item.verification,'request_id':'new'}
        if state=='missing': return None
        return NS(package_type='UNIT' if state=='unit' else None if state=='unknown_type' else 'BOX',
            gtin=GTIN, children=[] if state in {'unit','unknown_type'} else
            CHILDREN+['010463033960870521OTHER'] if state=='mixed' else CHILDREN)
    monkeypatch.setattr(service,'ChestnyZnakService',lambda **kwargs:NS(get_code_info=get_info))
    monkeypatch.setattr(service,'publish_scan',AsyncMock())
    learn=AsyncMock(return_value=True)
    monkeypatch.setattr(formats,'learn',learn)
    await service.run(str(item.id),str(doc.user_id),rid)
    if state in {'deleted','finished','newer'}:
        db.commit.assert_not_awaited()
        learn.assert_not_awaited()
    elif state in {'missing','unknown_type','mixed','conflict'}:
        assert item.status==ScanStatus.invalid and item.box_quantity is None
        assert item.gtin=='02008020841545' and item.error_message
        learn.assert_not_awaited()
    else:
        assert item.gtin==GTIN and item.moysklad_product_id=='chief'
        assert item.status==ScanStatus.scanned and item.verified_at is None and item.error_message is None
        assert item.box_quantity==(None if state=='unit' else 2)
        assert item.verification['packaging_rule_saved']
        learn.assert_awaited_once()


def test_format_separates_ai02_and_unit_for_same_gtin():
    assert formats.code_format(RAW)=='gs1_ai02'
    assert formats.code_format('(02)00802084154583(13)260930(10)2MSCS(21)1101221(37)10')=='gs1_ai02'
    assert formats.code_format(f'01{GTIN}21TEST\x1d93ABCD')=='gs1_ai01'
    assert formats.code_format(CHILDREN[0])=='bare_gtin'
    assert formats.code_format('unknown') is None


async def test_invalid_gtin_cannot_poison_format_rule():
    db=NS(execute=AsyncMock())
    assert not await formats.learn(db,'020080208415458413260930102MSCS2111012213710',
        {'gtin':GTIN,'package_type':'BOX','box_quantity':10})
    db.execute.assert_not_awaited()


def test_sscc_requires_complete_composition_and_no_unit_gtin_is_guessed():
    with pytest.raises(ValueError):
        service.checked_metadata(NS(package_type='BOX',children=[],gtin=GTIN),GTIN)
    with pytest.raises(ValueError):
        service.checked_metadata(NS(package_type='UNIT',children=[],gtin=None),GTIN)


async def test_pc_and_tsd_routes_reject_a_scan_outside_the_owned_document(monkeypatch):
    from app.api import scans, tsd
    db=NS(execute=AsyncMock(return_value=Result(None)))
    with pytest.raises(HTTPException) as err:
        await scans.recheck_packaging(uuid4(),NS(id=uuid4()),db)
    assert err.value.status_code==404
    owned=AsyncMock()
    monkeypatch.setattr(tsd,'_owned_tsd_document',owned)
    with pytest.raises(HTTPException) as err:
        await tsd.recheck_tsd_packaging(uuid4(),uuid4(),NS(user_id=uuid4()),db)
    assert err.value.status_code==404
    owned.assert_awaited_once()


async def test_completed_document_guard_prevents_recheck(monkeypatch):
    db=NS(refresh=AsyncMock(),commit=AsyncMock())
    monkeypatch.setattr(service,'editable_document',AsyncMock(side_effect=HTTPException(409,'Документ завершён')))
    with pytest.raises(HTTPException):
        await service.start(db,scan(),uuid4())
    db.commit.assert_not_awaited()


@pytest.mark.parametrize('learned', [False,True])
async def test_learned_aggregate_bypasses_unit_serial_rejection_only_to_queue_live_discovery(monkeypatch,learned):
    from app.api import scans
    doc=NS(kind='demand',plan=[{'gtin':GTIN,'product_id':'chief','product_name':'Afzal'}])
    monkeypatch.setattr(scans,'editable_document',AsyncMock(return_value=doc))
    monkeypatch.setattr(formats,'known_aggregate',AsyncMock(return_value=learned))
    delay=Mock(); monkeypatch.setattr(tasks.resolve_scan_packaging_task,'delay',delay)
    db=NS(execute=AsyncMock(return_value=Result(None)),add=Mock(),commit=AsyncMock(),refresh=AsyncMock())
    code=f'01{GTIN}13260930102MSCS2111012213710'
    item,_=await scans._create_scan_record(db,uuid4(),code,uuid4())
    if learned:
        assert item.status==ScanStatus.pending and item.box_quantity is None
        assert item.verified_at is None and item.verification['source']=='packaging_pending'
        delay.assert_called_once()
    else:
        assert item.status==ScanStatus.invalid
        delay.assert_not_called()
