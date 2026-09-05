"""Синхронизация ЭДО Saby → БД (EdoDocument/EdoMark) для контроля оборота марок.

Обход ленты СБИС.СписокИзменений КУРСОРОМ (см. project_mark_control_saby): постранично
от даты, дедуп документов по external_id. По исходящим реализациям скачиваем первичное
XML-вложение УПД и достаём марки нашим upd_parser. Курсор (последнее событие) сохраняем
в Integration для инкрементальной догрузки.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.core.security import decrypt_token
from app.db.models import EdoDocument, EdoMark, Integration
from app.services.saby import (
    SabyAuthError,
    SabyClient,
    SabyError,
    _extract_doc_list,
    last_event_cursor,
    parse_document,
    primary_upd_link,
)

# Предохранитель от бесконечного цикла (25/стр → до 25000 документов за один синк).
_MAX_PAGES = 1000


def _client(integ: Integration) -> Optional[SabyClient]:
    if integ and integ.saby_app_client_id:
        return SabyClient(
            app_client_id=integ.saby_app_client_id,
            app_secret=decrypt_token(integ.saby_app_secret) if integ.saby_app_secret else None,
            secret_key=decrypt_token(integ.saby_secret_key) if integ.saby_secret_key else None,
        )
    if integ and integ.saby_login and integ.saby_password:
        return SabyClient(login=integ.saby_login, password=decrypt_token(integ.saby_password), account=integ.saby_account)
    return None


async def _upsert_document(db, user_id, parsed: dict) -> EdoDocument:
    """Upsert EdoDocument по (user_id, external_id). Возвращает ORM-объект (свежий)."""
    ext = parsed["id"]
    row = (
        await db.execute(
            select(EdoDocument).where(
                EdoDocument.user_id == user_id, EdoDocument.external_id == ext
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = EdoDocument(user_id=user_id, external_id=ext)
        db.add(row)
    row.number = parsed.get("number")
    row.doc_date = parsed.get("date")
    row.direction = parsed.get("direction")
    row.doc_type = parsed.get("type")
    row.counterparty_inn = parsed.get("counterparty_inn")
    row.counterparty_name = parsed.get("counterparty_name")
    row.state_code = parsed.get("state_code")
    row.state_name = parsed.get("state_name")
    if parsed.get("mark_state"):
        row.mark_state = parsed["mark_state"]
    return row


async def _save_marks(db, doc: EdoDocument, user_id, codes: list[str]) -> int:
    """Сохранить марки документа (idempotent upsert по (document_id, cis_canonical))."""
    from app.services.chestnyznak import strip_ai_brackets, extract_gtin, normalize_gtin_key

    def canon(code: str) -> str:
        s = strip_ai_brackets(code or "").strip()
        s = s.split("\x1d", 1)[0]
        import re as _re
        return _re.sub(r"(?i)%c1", "", s).strip()

    n = 0
    for raw in codes:
        key = canon(raw)
        if not key:
            continue
        stmt = pg_insert(EdoMark).values(
            document_id=doc.id,
            user_id=user_id,
            cis_raw=raw,
            cis_canonical=key,
            gtin=normalize_gtin_key(extract_gtin(raw)),
        ).on_conflict_do_nothing(constraint="ix_edo_marks_doc_cis")
        await db.execute(stmt)
        n += 1
    return n


async def sync_user(db, integ: Integration, *, date_from: str, date_to: Optional[str] = None,
                    use_cursor: bool = True) -> dict:
    """Синхронизировать документы ЭДО пользователя за период. date_from/to — «ДД.ММ.ГГГГ ЧЧ.ММ.СС».

    use_cursor=True — продолжить с сохранённого курсора Integration (инкремент); иначе с date_from.
    """
    client = _client(integ)
    if client is None:
        return {"error": "Saby не подключён"}
    user_id = integ.user_id
    header, token = await client.authenticate()
    auth = (header, token)

    event_id = integ.saby_last_event_id if use_cursor else None
    event_dt = integ.saby_last_event_dt if use_cursor else None
    doc_id = integ.saby_last_doc_id if use_cursor else None
    cur_from = event_dt or date_from

    pages = docs_seen = out_docs = marks_saved = parsed_docs = 0
    seen_ext: set[str] = set()

    for _ in range(_MAX_PAGES):
        try:
            result = await client.changes_page(
                auth, date_from=cur_from, date_to=date_to,
                event_id=event_id, doc_id=doc_id, with_extension=True,
            )
        except SabyAuthError:
            header, token = await client.authenticate()
            auth = (header, token)
            result = await client.changes_page(
                auth, date_from=cur_from, date_to=date_to,
                event_id=event_id, doc_id=doc_id, with_extension=True,
            )
        docs = _extract_doc_list(result)
        pages += 1
        if not docs:
            break

        for d in docs:
            parsed = parse_document(d)
            ext = parsed.get("id")
            if not ext or ext in seen_ext:
                continue
            seen_ext.add(ext)
            docs_seen += 1
            # Интересуют исходящие реализации (УПД) с марками.
            is_out_realiz = parsed.get("direction") == "Исходящий" and "реализац" in (parsed.get("type") or "").lower()
            row = await _upsert_document(db, user_id, parsed)
            await db.flush()
            if is_out_realiz:
                out_docs += 1
                if not row.marks_parsed:
                    link = primary_upd_link(d)
                    if link:
                        try:
                            raw = await client.download(auth, link)
                            from app.services.upd_parser import parse_upd_503
                            upd = parse_upd_503(raw)
                            codes = [c for p in upd.positions for c in (p.codes or [])]
                            saved = await _save_marks(db, row, user_id, codes)
                            row.codes_total = saved
                            row.marks_parsed = True
                            marks_saved += saved
                            parsed_docs += 1
                        except Exception as exc:
                            logger.warning("edo_sync.parse_failed", ext=ext, error=str(exc))
        await db.commit()

        # Курсор следующей страницы
        eid, edt, did = last_event_cursor(docs)
        nav = result.get("Навигация") if isinstance(result, dict) else {}
        has_more = str((nav or {}).get("ЕстьЕще") or "").lower() == "да"
        if eid:
            event_id, event_dt, doc_id, cur_from = eid, edt, did, (edt or cur_from)
            integ.saby_last_event_id = eid
            integ.saby_last_event_dt = edt
            integ.saby_last_doc_id = did
        integ.saby_synced_at = datetime.now(timezone.utc)
        await db.commit()
        if not has_more:
            break

    logger.info("edo_sync.done", user_id=str(user_id), pages=pages, docs=docs_seen,
                out_docs=out_docs, parsed=parsed_docs, marks=marks_saved)
    return {"pages": pages, "documents": docs_seen, "out_realizations": out_docs,
            "parsed_docs": parsed_docs, "marks_saved": marks_saved}
