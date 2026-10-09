import asyncio,json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import httpx
from app.services.chestnyznak import CisCheck,ChestnyZnakService
from app.services.scan_verification import check_scans,acceptable
from app.services import chestnyznak,cz_logger,cz_pg_cache

async def test_1500_codes_use_100_code_batches_with_at_most_three_in_flight(monkeypatch):
    real=httpx.AsyncClient
    active=peak=0
    sizes=[]
    async def respond(request):
        nonlocal active,peak
        codes=json.loads(request.content)
        sizes.append(len(codes));active+=1;peak=max(peak,active)
        await asyncio.sleep(0.005)
        active-=1
        return httpx.Response(200,json=[{'cisInfo':{'requestedCis':code,'status':'INTRODUCED','ownerInn':'123','gtin':'04640195516144'}} for code in codes])
    monkeypatch.setattr(chestnyznak.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(respond),**kw))
    monkeypatch.setattr(cz_logger,'log_cz_request',AsyncMock())
    cache=AsyncMock()
    monkeypatch.setattr(cz_pg_cache,'set_cached_pg',cache)
    codes=['010464019551614421'+f'{i:07d}' for i in range(1500)]
    result=await ChestnyZnakService('test',mock=False,product_groups=['otp']).check_codes(codes)
    assert sizes==[100]*15
    assert peak==3
    assert [c.code for c in result]==codes
    assert all(c.owner_inn=='123' for c in result)
    assert cache.await_count==15

async def test_packages_share_leaf_batches_and_keep_failures_local():
    scans=[NS(code=f'parent{i}',is_box=False,child_codes=['stale']) for i in range(5)]
    parents=[CisCheck(code=s.code,found=True,status='INTRODUCED',package_type='GROUP') for s in scans]
    active=peak=0
    async def info(code):
        nonlocal active,peak
        active+=1;peak=max(peak,active)
        await asyncio.sleep(0.005)
        active-=1
        if code=='parent4':raise TimeoutError()
        return NS(children=[code+'leaf','shared'])
    async def checks(codes):
        if codes==[s.code for s in scans]:return parents
        assert codes==['parent0leaf','shared','parent1leaf','parent2leaf','parent3leaf']
        return [CisCheck(code=c,found=True,status='WITHDRAWN' if c=='parent2leaf' else 'INTRODUCED') for c in codes]
    cz=NS(mock=False,token='test',get_code_info=AsyncMock(side_effect=info),check_codes=AsyncMock(side_effect=checks))
    results=await check_scans(cz,scans)
    assert peak==3 and cz.check_codes.await_count==2
    assert acceptable(results['parent0']) and acceptable(results['parent1']) and acceptable(results['parent3'])
    assert results['parent2'].verification['child_issues'][0]['code']=='parent2leaf'
    assert not acceptable(results['parent2'])
    assert results['parent4'].uncertain and results['parent4'].verification['checked_at'] is None
