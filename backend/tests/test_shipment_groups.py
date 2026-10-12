from unittest.mock import AsyncMock
from app.services.moysklad import MoySkladService
from app.api.documents import PlanItem
async def test_plan_preserves_ms_folder_and_full_path_without_extra_requests():
    ms=MoySkladService('test')
    ms._load_positions_rows=AsyncMock(return_value=[{'quantity':1,'assortment':{'id':'p','name':'Товар','pathName':'Табак/Бренд А','productFolder':{'meta':{'href':'https://api.moysklad.ru/api/remap/1.2/entity/productfolder/folder'}}}}])
    plan=await ms.build_plan('demand','doc')
    assert plan[0]['folder_id']=='folder'
    assert PlanItem(**plan[0]).model_dump()['folder_name']=='Табак/Бренд А'
    ms._load_positions_rows.assert_awaited_once()
async def test_plan_without_group_remains_compatible():
    ms=MoySkladService('test')
    ms._load_positions_rows=AsyncMock(return_value=[{'quantity':1,'assortment':{'id':'p','name':'Товар'}}])
    plan=await ms.build_plan('demand','doc')
    assert plan[0]['folder_name'] is None
