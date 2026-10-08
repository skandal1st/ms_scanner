import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.dialects import sqlite

from app.api.acceptance import _edo_incoming_query
from app.db.models import EdoDocument
from app.services import edo_sync
from app.services.upd_parser import UpdParseError


def xml(mark=''):
    return f'<Файл><Документ><ТаблСчФакт><СведТов НаимТов="Товар" КолТов="1">{mark}</СведТов></ТаблСчФакт></Документ></Файл>'.encode()


def test_list_and_counter_filter_processed_plain_unknown_foreign_and_imported():
    user = uuid4()
    connection = sqlite3.connect(':memory:')
    integer = {'state_code', 'marks_parsed', 'codes_total'}
    columns = ', '.join(f'"{column.name}" {"INTEGER" if column.name in integer else "TEXT"}'
                        for column in EdoDocument.__table__.columns)
    connection.execute(f'CREATE TABLE edo_documents ({columns})')
    def add(external_id, state=10, checked=True, codes=3, imported=None, owner=user, direction='Входящий', link='xml'):
        connection.execute('INSERT INTO edo_documents (external_id, user_id, direction, state_code, marks_parsed, codes_total, accepted_document_id, upd_link) VALUES (?,?,?,?,?,?,?,?)',
            (external_id, owner.hex, direction, state, checked, codes, imported, link))
    add('new', state=1)
    add('processing')
    add('awaiting_signature', state=23)
    for state in (7, 9, 19, 20, 22, 6, None):
        add(f'excluded-{state}', state=state)
    add('plain', codes=0)
    add('unverified', checked=False)
    add('imported', imported=uuid4().hex)
    add('foreign', owner=uuid4())
    add('outgoing', direction='Исходящий')
    add('no-xml', link=None)
    query = _edo_incoming_query(user).with_only_columns(EdoDocument.external_id)
    compiled = lambda statement: str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
    assert {row[0] for row in connection.execute(compiled(query))} == {'new', 'processing', 'awaiting_signature'}
    assert connection.execute(compiled(select(func.count()).select_from(query.subquery()))).fetchone()[0] == 3
    connection.execute('UPDATE edo_documents SET state_code=7 WHERE external_id="processing"')
    assert connection.execute(compiled(select(func.count()).select_from(query.subquery()))).fetchone()[0] == 2
    connection.close()


def test_marks_are_confirmed_from_xml_including_package_only_upd():
    assert edo_sync.incoming_mark_count(xml()) == 0
    assert edo_sync.incoming_mark_count(xml('<НомСредИдентТов><КИЗ>010460123456789021abc</КИЗ></НомСредИдентТов>')) == 1
    assert edo_sync.incoming_mark_count(xml('<НомСредИдентТов НомУпак="00123456789012345678"/>')) == 1
    with pytest.raises(UpdParseError):
        edo_sync.incoming_mark_count(b'not xml')


def test_attachment_changes_reset_verification_but_status_only_events_do_not():
    row = NS(upd_link='old', marks_parsed=True, codes_total=3)
    edo_sync._set_incoming_link(row, None)
    assert row.marks_parsed and row.codes_total == 3
    edo_sync._set_incoming_link(row, 'old')
    assert row.marks_parsed
    edo_sync._set_incoming_link(row, 'new')
    assert row.upd_link == 'new' and not row.marks_parsed and row.codes_total == 0


async def test_xml_failure_stays_unverified_for_retry():
    rows = [NS(external_id='plain', upd_link='plain', marks_parsed=False, codes_total=0),
            NS(external_id='marked', upd_link='marked', marks_parsed=False, codes_total=0),
            NS(external_id='failed', upd_link='failed', marks_parsed=False, codes_total=0)]
    result = NS(scalars=lambda: NS(all=lambda: rows))
    db = NS(execute=AsyncMock(return_value=result), commit=AsyncMock())
    client = NS(download=AsyncMock(side_effect=[xml(), xml('<НомСредИдентТов КИЗ="mark"/>'), TimeoutError('test timeout')]))
    assert await edo_sync.check_incoming_marks(db, client, ('header', 'token'), uuid4()) == 2
    assert rows[0].marks_parsed and rows[0].codes_total == 0
    assert rows[1].marks_parsed and rows[1].codes_total == 1
    assert not rows[2].marks_parsed


async def test_state_only_event_updates_cached_status_and_keeps_import(monkeypatch):
    row = NS(state_code=10, state_name='В обработке', accepted_document_id=uuid4())
    db = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: row)))
    await edo_sync._upsert_document(db, uuid4(), {'id': 'doc', 'state_code': 7, 'state_name': 'Завершён'})
    assert row.state_code == 7 and row.accepted_document_id
    await edo_sync._upsert_document(db, uuid4(), {'id': 'doc'})
    assert row.state_code == 7


async def test_manual_scan_applies_later_status_even_without_attachment(monkeypatch):
    row = NS(upd_link='xml', marks_parsed=True, codes_total=1)
    client = NS(authenticate=AsyncMock(return_value=('header', 'token')),
        changes_page=AsyncMock(return_value={'Документ': [
            {'Идентификатор': 'doc', 'Направление': 'Входящий', 'Состояние': {'Код': '10'}},
            {'Идентификатор': 'doc', 'Направление': 'Входящий', 'Состояние': {'Код': '7'}},
        ]}))
    monkeypatch.setattr(edo_sync, '_client', lambda integration: client)
    upsert = AsyncMock(return_value=row)
    monkeypatch.setattr(edo_sync, '_upsert_document', upsert)
    monkeypatch.setattr(edo_sync, 'check_incoming_marks', AsyncMock())
    await edo_sync.scan_incoming_upds(NS(commit=AsyncMock()), NS(user_id=uuid4()))
    assert upsert.await_count == 2
    assert upsert.await_args.args[2]['state_code'] == 7
