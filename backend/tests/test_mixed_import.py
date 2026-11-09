from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.mixed_import import Resolver, resolve_import, normalize_codes
from app.services.mark_search import mark_key

A = '010464027597309621UNIT001'
B = '010464027597309621UNIT002'
BLOCK = '010464027597309621BLOCK001'
SSCC = '00046402759700334510'


def scan(code, children=None, kind='UNIT'):
    return NS(id=uuid4(), code=code, child_codes=children, package_type=kind,
              is_barcode=False, is_box=kind == 'BOX', box_quantity=len(children) if children else None)


async def test_file_overlap_uses_serial_identity_and_keeps_case_distinct():
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=[A, B], package_type='GROUP')))
    parents, units, occurrences, conflicts, new, _ = await resolve_import(
        [BLOCK, A, B, A + '\x1d91abcd'], [scan(A)], cz)
    assert not parents and not conflicts
    assert set(units) == {mark_key(A), mark_key(B)}
    assert occurrences == 5 and new == 1
    assert mark_key(A) != mark_key(A.lower())


async def test_existing_block_and_its_loaded_leaf_are_represented_once():
    parent = scan(BLOCK, [A, B], 'GROUP')
    parents, units, occurrences, conflicts, new, parent_units = await resolve_import(
        [B, B], [parent, scan(A)], None)
    assert parents == [parent] and not conflicts and new == 0 and occurrences == 2
    assert set(units.values()) == {A, B}
    assert parent_units[str(parent.id)] == [A, B]


@pytest.mark.parametrize('info', [None, NS(children=[], package_type='GROUP'), NS(children=[], package_type=None)])
async def test_unknown_composition_or_type_is_a_conflict(info):
    cz = NS(get_code_info=AsyncMock(return_value=info))
    result = await resolve_import([BLOCK], [], cz)
    assert result[3] and result[3][0]['code'] == BLOCK


async def test_sscc_and_block_flatten_to_the_same_leaf_set():
    cz = NS(unpack_box=AsyncMock(return_value=[A, B]),
            get_code_info=AsyncMock(return_value=NS(children=[A, B], package_type='GROUP')))
    _, units, count, conflicts, new, _ = await resolve_import([SSCC, BLOCK, A], [], cz)
    assert not conflicts and set(units.values()) == {A, B} and count == 5 and new == 2


async def test_incomplete_children_and_cyclic_composition_are_rejected():
    result = await resolve_import([BLOCK], [scan(BLOCK, [BLOCK], 'GROUP')], None)
    assert result[3]
    result = await resolve_import([SSCC], [scan(SSCC, [SSCC], 'BOX')], None)
    assert result[3]


async def test_external_failure_does_not_fall_back_to_whole_block():
    cz = NS(get_code_info=AsyncMock(side_effect=TimeoutError()))
    result = await resolve_import([BLOCK], [], cz)
    assert not result[1] and result[3]


async def test_inconsistent_cached_quantity_does_not_hide_missing_units():
    parent = scan(BLOCK, [A], 'GROUP')
    parent.box_quantity = 10
    result = await resolve_import([B], [parent], None)
    assert result[3]


def test_empty_and_oversized_lists_are_rejected():
    from fastapi import HTTPException
    for codes in [[], [' '], [A] * 5001, ['x' * 2049]]:
        with pytest.raises(HTTPException):
            normalize_codes(codes)


@pytest.mark.parametrize('target', ['blocks', 'units'])
async def test_manufacturer_file_box_blocks_and_all_unit_marks_keeps_one_level(target):
    c = '010464027597309621UNIT003'
    d = '010464027597309621UNIT004'
    block2 = '010464027597309621BLOCK002'
    contents = {BLOCK: [A, B], block2: [c, d]}
    async def info(code):
        return NS(children=contents.get(code, []), package_type='GROUP' if code in contents else 'UNIT')
    cz = NS(unpack_box=AsyncMock(return_value=[A, B, c, d]), get_code_info=AsyncMock(side_effect=info))
    result = await resolve_import([SSCC, BLOCK, block2, A, B, c, d, BLOCK], [], cz, target)
    assert not result[3] and set(result[1].values()) == {A, B, c, d}
    if target == 'blocks':
        assert {block['code'] for block in result[6].values()} == {BLOCK, block2}
        assert all(block['code'] != SSCC for block in result[6].values())
    else:
        assert len(result) == 6


async def test_blocks_mode_rejects_uncovered_packs_instead_of_dropping_them():
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=[], package_type='UNIT')))
    result = await resolve_import([A], [], cz, 'blocks')
    assert result[3] and result[6] == {}
    assert 'нет подтверждённого блока' in result[3][0]['message']


async def test_blocks_mode_rejects_overlapping_blocks():
    block2 = BLOCK + '2'
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=[A, B], package_type='GROUP')))
    result = await resolve_import([BLOCK, block2], [], cz, 'blocks')
    assert any('Два разных блока' in c['message'] for c in result[3])


@pytest.mark.parametrize('with_block', [False, True])
async def test_blocks_and_loose_keep_uncovered_units_without_conflict(with_block):
    loose = '010464027597309621LOOSE001'
    async def info(code):
        return NS(children=[A, B] if code == BLOCK else [], package_type='GROUP' if code == BLOCK else 'UNIT')
    cz = NS(get_code_info=AsyncMock(side_effect=info))
    codes = [BLOCK, A, B, loose, loose] if with_block else [A, loose]
    result = await resolve_import(codes, [], cz, 'blocks_loose')
    assert not result[3]
    assert set(result[1].values()) == ({A, B, loose} if with_block else {A, loose})
    covered = {mark_key(code) for block in result[6].values() for code in block['children']}
    assert {code for key, code in result[1].items() if key not in covered} == ({loose} if with_block else {A, loose})


async def test_hybrid_unknown_or_overlapping_block_is_still_a_conflict():
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=[], package_type='GROUP')))
    result = await resolve_import([BLOCK], [], cz, 'blocks_loose')
    assert result[3]
    cz.get_code_info.return_value = NS(children=[A, B], package_type='GROUP')
    result = await resolve_import([BLOCK, BLOCK + '2'], [], cz, 'blocks_loose')
    assert any('Два разных блока' in c['message'] for c in result[3])


@pytest.mark.parametrize('target', ['boxes'])
async def test_boxes_only_deduplicates_manufacturer_list_and_existing_marks(target):
    cz = NS(unpack_box=AsyncMock(return_value=[A, B]),
            get_code_info=AsyncMock(return_value=NS(children=[A, B], package_type='GROUP')))
    result = await resolve_import([SSCC, BLOCK, A, B, SSCC], [scan(A)], cz, target)
    assert not result[3]
    assert set(result[1].values()) == {A, B}
    assert [b['code'] for b in result[6].values()] == [SSCC]


@pytest.mark.parametrize('contents, extra, message', [
    ([A, B], ['010464027597309621LOOSE001'], 'нет подтверждённого короба'),
    ([], [], 'Состав упаковки неизвестен'),
    ([A, '010466032120585821UNIT003'], [], 'разные товары'),
])
async def test_boxes_only_never_silently_drops_uncovered_or_unknown_marks(contents, extra, message):
    cz = NS(unpack_box=AsyncMock(return_value=contents),
            get_code_info=AsyncMock(return_value=NS(children=[], package_type='UNIT')))
    result = await resolve_import([SSCC, *extra], [], cz, 'boxes')
    assert any(message in c['message'] for c in result[3])


async def test_boxes_only_rejects_two_boxes_with_overlapping_contents():
    cz = NS(unpack_box=AsyncMock(return_value=[A, B]))
    result = await resolve_import([SSCC, '00046402759700392565'], [], cz, 'boxes')
    assert any('Два разных короба' in c['message'] for c in result[3])


async def test_boxes_only_requires_sscc_instead_of_mislabeling_gs1_aggregate():
    cz = NS(get_code_info=AsyncMock(return_value=NS(children=[A, B], package_type='BOX')))
    result = await resolve_import([BLOCK], [], cz, 'boxes')
    assert any('Для короба нужен SSCC' in c['message'] for c in result[3])
