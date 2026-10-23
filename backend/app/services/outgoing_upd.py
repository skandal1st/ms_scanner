"""Draft-only Saby uploads. No signing, preparation or workflow action methods.

DB row lock serializes callers; uploading intent is committed before the write.
Uncertain outcomes are reconciled by reads only, never by blind replay.
"""
import base64
import hashlib
import json
from urllib.parse import urlparse
from uuid import UUID, uuid5

import httpx
from sqlalchemy import select

from app.db.models import OutgoingUpd
from app.schemas.upd_export import UpdExportOptions
from app.services.saby import SabyError, SabyRpcError, SabyAuthError, SabyDocumentNotFound
from app.services.upd_export import generate_upd


def attachment_id(row):
    return str(uuid5(UUID(str(row.id)), "upd-attachment"))


def connection_key(integration):
    return hashlib.sha256(json.dumps([integration.saby_app_client_id, integration.saby_login,
                                      integration.saby_account], ensure_ascii=False).encode()).hexdigest()


def trusted_saby_url(value):
    parsed = urlparse(value or "")
    host = parsed.hostname or ""
    return value if parsed.scheme == 'https' and any(host == d or host.endswith('.' + d) for d in ('sbis.ru', 'saby.ru')) else None


def response(row):
    return {"id": str(row.id), "status": row.status, "url": trusted_saby_url(row.external_url),
            "external_state": row.external_state, "error": row.error_message,
            "filename": row.filename, "options": row.options}


def party(data):
    if len(data['inn']) == 12:
        return {'СвФЛ': {'ИНН': data['inn'], 'Фамилия': data['last_name'], 'Имя': data['first_name'],
                         **({'Отчество': data['middle_name']} if data.get('middle_name') else {})}}
    return {'СвЮЛ': {'ИНН': data['inn'], 'КПП': data['kpp'], 'Название': data['name']}}


def payload(row):
    options = UpdExportOptions.model_validate(row.options)
    return {'Документ': {'Идентификатор': str(row.id), 'Тип': 'ДокОтгрИсх',
            'Номер': options.number, 'Дата': options.document_date.strftime('%d.%m.%Y'),
            'Примечание': 'Создано в Скандате. Только черновик, без подписи и отправки.',
            'НашаОрганизация': party(row.options['seller']),
            'Контрагент': {**party(row.options['buyer']), 'Идентификатор': options.receiver_id},
            'Вложение': [{'Идентификатор': attachment_id(row),
                         'Файл': {'Имя': row.filename, 'ДвоичныеДанные': base64.b64encode(row.xml_text.encode('cp1251')).decode('ascii')}}]}}


async def read(client, auth, row):
    remote = await client.call('СБИС.ПрочитатьДокумент',
                              {'Документ': {'Идентификатор': str(row.id)}}, auth)
    if not isinstance(remote, dict) or str(remote.get('Идентификатор')) != str(row.id):
        raise SabyError('СБИС не подтвердил идентификатор документа')
    return remote


async def verify(client, auth, row, remote):
    if not isinstance(remote, dict) or str(remote.get('Идентификатор')) != str(row.id):
        raise SabyError('СБИС вернул другой документ. Повторная запись заблокирована')
    if remote.get('ЧастичныеДанные') == 'Да':
        raise SabyError('СБИС вернул неполные данные. Проверка черновика не завершена')
    state = remote.get('Состояние') or {}
    if str(state.get('Код')) != '0':
        raise SabyError('Документ в СБИС уже вышел из черновика. Повторная запись заблокирована')
    attachments = remote.get('Вложение') or []
    matches = [a for a in attachments if a.get('Идентификатор') == attachment_id(row)]
    if len(matches) != 1 or len(attachments) != 1 or matches[0].get('Подпись'):
        raise SabyError('Состав или подписи документа в СБИС изменились. Повторная запись заблокирована')
    file = matches[0].get('Файл') or {}
    if file.get('Имя') != row.filename:
        raise SabyError('Имя XML в СБИС не совпадает с загруженным УПД')
    if file.get('ДвоичныеДанные'):
        xml = base64.b64decode(file['ДвоичныеДанные'], validate=True)
    elif trusted_saby_url(file.get('Ссылка')):
        xml = await client.download(auth, file['Ссылка'])
    else:
        raise SabyError('СБИС не вернул XML для проверки загрузки')
    if hashlib.sha256(xml).hexdigest() != row.xml_sha256:
        raise SabyError('XML в СБИС отличается от загруженного УПД. Повторная запись заблокирована')
    return trusted_saby_url(remote.get('СсылкаДляНашаОрганизация')), state.get('Название') or 'Документ редактируется'


async def deliver(db, row_id, client, auth, *, replacement=None):
    row = await db.scalar(select(OutgoingUpd).where(OutgoingUpd.id == row_id).with_for_update()
                          .execution_options(populate_existing=True))
    if row.status == 'created':
        await db.commit()
        return row
    may_write = row.status in ('prepared', 'failed')
    # Hold lock through the initial read so a concurrent retry cannot obtain
    # a second permission to write. Commit upload intent before the write.
    try:
        try:
            remote = await read(client, auth, row)
        except SabyDocumentNotFound:
            remote = None
        if remote is None:
            if not may_write:
                raise SabyError('Загрузка ещё не подтверждена СБИС. Проверьте позже; повторная запись отключена для защиты от дублей')
            if row.status == 'failed' and replacement:
                source, options = replacement
                filename, xml = generate_upd(source, options, file_uuid=row.id)
                row.filename, row.xml_text = filename, xml.decode('cp1251')
                row.xml_sha256 = hashlib.sha256(xml).hexdigest()
                row.options = options.model_dump(mode='json')
            row.status, row.error_message = 'uploading', None
            await db.commit()
            try:
                await client.call('СБИС.ЗаписатьДокумент', payload(row), auth)
            except (SabyRpcError, SabyAuthError) as exc:
                # Definite rejection: retry may rebuild XML only after a fresh
                # definitive not-found response. Transport failures stay uncertain.
                row.status, row.error_message = 'failed', str(exc)
                if isinstance(exc, SabyAuthError):
                    from app.api.mark_control import _invalidate_auth
                    await _invalidate_auth(row.user_id)
                await db.commit()
                return row
            remote = await read(client, auth, row)
        url, state = await verify(client, auth, row, remote)
        row.status, row.error_message, row.external_url, row.external_state = 'created', None, url, state
        await db.commit()
    except (SabyError, httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, SabyAuthError):
            from app.api.mark_control import _invalidate_auth
            await _invalidate_auth(row.user_id)
        await db.rollback()
        # Another request may have completed while this reconciliation failed.
        row = await db.scalar(select(OutgoingUpd).where(OutgoingUpd.id == row_id).with_for_update()
                              .execution_options(populate_existing=True))
        if row.status != 'created':
            if row.status not in ('prepared', 'failed'):
                row.status = 'uncertain'
            row.error_message = str(exc) if isinstance(exc, SabyError) else 'Нет подтверждения загрузки от СБИС. Проверьте результат повторно'
            await db.commit()
        else:
            await db.commit()
    return row
