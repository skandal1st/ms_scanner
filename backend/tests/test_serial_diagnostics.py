import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api import scans
from app.services import serial_diagnostics


@pytest.mark.parametrize('raw', ['475', 'GG', '00' * 4097])
def test_only_bounded_byte_pairs_are_accepted(raw):
    with pytest.raises(ValidationError):
        scans.CreateScanRequest(document_id=uuid4(), code='test', serial_raw_hex=raw)


@pytest.mark.skipif(not os.getenv('TEST_SERIAL_REDIS_URL'), reason='isolated Redis required')
async def test_com_attempts_round_trip_and_remain_separate_from_manual_scans(monkeypatch):
    monkeypatch.setattr(serial_diagnostics.settings, 'REDIS_URL', os.environ['TEST_SERIAL_REDIS_URL'])
    doc = SimpleNamespace(id=uuid4(), plan=[])
    user = SimpleNamespace(id=uuid4())
    code = '010462718329680621zNda7\x1d93ryrd'
    raw = code.replace('zNda7', 'zNGSda7').replace('\x1d', '\xf8').encode('latin1').hex()
    row = scans.ScanResponse(id=uuid4(), document_id=doc.id, code=code, status='scanned',
                             scanned_at=datetime.now(timezone.utc))
    monkeypatch.setattr(scans, 'editable_document', AsyncMock(return_value=doc))
    monkeypatch.setattr(scans, '_create_scan_record', AsyncMock(return_value=(row, True)))
    ownership = Mock()
    ownership.scalar_one_or_none.return_value = doc
    app = FastAPI()
    app.include_router(scans.router)
    app.dependency_overrides[scans.get_db] = lambda: SimpleNamespace(execute=AsyncMock(return_value=ownership))
    app.dependency_overrides[scans.get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        body = {'document_id': str(doc.id), 'code': code}
        for attempt in [{'serial_raw_hex': raw}, {'serial_raw_hex': code.encode().hex()}, {}]:
            response = await client.post('/scans/', json={**body, **attempt})
            assert response.status_code == 201 and response.json()['duplicate']
        url = f'/scans/serial-diagnostics/{doc.id}'
        response = await client.post(url, json={'code': code})
        assert response.status_code == 200
        assert [(r['raw_hex'], r['received_code']) for r in response.json()] == [
            (code.encode().hex(), code), (raw, code)]
        ownership.scalar_one_or_none.return_value = None
        assert (await client.post(url, json={'code': code})).status_code == 404
