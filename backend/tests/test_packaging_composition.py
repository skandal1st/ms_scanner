from app.services.packaging_composition import normalize_tree, walk, compose_forest
import pytest

BOX = '00046402759700334510'
BLOCK = '010464027597309621BLOCK001'
A = '010464027597309621UNIT001'
B = '010464027597309621UNIT002'


def test_real_edges_and_explicit_kinds_are_preserved():
    tree = normalize_tree(BOX, {BLOCK: [A, B]}, 'BOX', {BLOCK: 'GROUP'})
    assert tree['kind'] == 'BOX'
    assert tree['children'][0]['code'] == BLOCK and tree['children'][0]['kind'] == 'GROUP'
    assert [node['code'] for node in tree['children'][0]['children']] == [A, B]
    assert len(list(walk(tree))) == 4


def test_unknown_middle_level_is_not_guessed_from_depth_or_gtin():
    tree = normalize_tree(BOX, {BLOCK: [A, B]}, 'BOX')
    assert tree['children'][0]['kind'] == 'UNKNOWN'
    tree = normalize_tree(BOX, [A, B], 'BOX')
    assert [n['kind'] for n in tree['children']] == ['UNIT', 'UNIT']
    assert all(n['code'] != BLOCK for n in walk(tree))


def test_empty_malformed_and_excessively_deep_trees_are_rejected():
    for raw in [[], [123], 'invalid']:
        with pytest.raises(ValueError):
            normalize_tree(BOX, raw, 'BOX')
    raw = [A]
    for index in range(20):
        raw = {f'group{index}': raw}
    with pytest.raises(ValueError):
        normalize_tree(BOX, raw, 'BOX')


def test_only_confirmed_parent_edges_join_separate_observations():
    box = normalize_tree(BOX, [BLOCK], 'BOX')
    block = normalize_tree(BLOCK, [A, B], 'GROUP')
    roots = compose_forest([box, block])
    assert len(roots) == 1 and roots[0]['children'][0]['kind'] == 'GROUP'
    assert [n['code'] for n in roots[0]['children'][0]['children']] == [A, B]
    flat_box = normalize_tree(BOX, [A, B], 'BOX')
    roots = compose_forest([flat_box, block])
    assert len(roots) == 2  # Matching leaf sets do not prove box -> block.


async def test_unpack_keeps_raw_hierarchy_and_existing_flattened_return(monkeypatch):
    import httpx
    from unittest.mock import AsyncMock
    from app.services.chestnyznak import ChestnyZnakService
    from app.services import cz_logger
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={BOX: {BLOCK: [A, B]}}))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    monkeypatch.setattr(cz_logger, 'log_cz_request', AsyncMock())
    cz = ChestnyZnakService(token='test', mock=False, product_groups=['tobacco'])
    assert await cz.unpack_box(BOX) == [A, B]
    assert cz.aggregate_trees[BOX]['raw'] == {BLOCK: [A, B]}
