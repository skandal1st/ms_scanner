from datetime import datetime,timezone
from uuid import uuid4
import pytest
from app.services.cz_status import cz_status_error,localize_cz_error
from app.api.scans import ScanResponse
from app.services.scan_verification import check_scans
from app.services.chestnyznak import CisCheck
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

@pytest.mark.parametrize('status', ['WITHDRAWN','RETIRED','WRITTEN_OFF','APPLIED','EMITTED','DISAGGREGATION','FUTURE_STATUS',None])
def test_status_errors_do_not_expose_technical_status(status):
    message=cz_status_error(status)
    assert any('А'<=c<='я' for c in message)
    assert not status or status not in message


def test_saved_error_and_nested_details_are_translated_without_mutating_data():
    verification={'source':'cz_info','child_issues':[{'code':'original-code','error':'Статус в ЧЗ: WITHDRAWN'}]}
    scan=ScanResponse(id=uuid4(),document_id=uuid4(),code='original-code',status='invalid',scanned_at=datetime.now(timezone.utc),error_message='Статус в ЧЗ: WITHDRAWN',verification=verification)
    response=scan.model_dump(mode='json')
    assert response['error_message']=='Марка выведена из оборота'
    assert response['verification']['child_issues'][0]=={'code':'original-code','error':'Марка выведена из оборота'}
    assert verification['child_issues'][0]['error']=='Статус в ЧЗ: WITHDRAWN'
    assert localize_cz_error('Проблемы: code: Статус: EMITTED')=='Проблемы: code: Марка выпущена, но ещё не введена в оборот'
    assert localize_cz_error('МойСклад: ошибка товара')=='МойСклад: ошибка товара'


async def test_bad_leaf_has_russian_error():
    client=NS(mock=False,token='test',get_code_info=AsyncMock(return_value=NS(children=['leaf'])),check_codes=AsyncMock(side_effect=[[CisCheck(code='box',found=True,status='INTRODUCED',package_type='GROUP')],[CisCheck(code='leaf',found=True,status='WITHDRAWN')]]))
    result=(await check_scans(client,[NS(code='box',is_box=False,child_codes=None)]))['box']
    assert result.verification['child_issues'][0]['error']=='Марка выведена из оборота'
    assert 'WITHDRAWN' not in result.error
