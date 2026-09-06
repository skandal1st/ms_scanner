import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, DateTime, Boolean, Integer, Numeric, Text, Enum,
    ForeignKey, JSON, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


def utcnow():
    return datetime.now(timezone.utc)


class DocumentStatus(str, PyEnum):
    draft = "draft"
    processing = "processing"
    accepted = "accepted"


class DocumentKind(str, PyEnum):
    """
    Тип МС-документа, к которому привязан наш Document.
    supply        — Поступление (приёмка): коды пишутся в МС (trackingCodes).
    demand        — Отгрузка покупателю.
    loss          — Списание.
    move          — Перемещение.
    salesreturn   — Возврат покупателя.
    Проверка КМ по умолчанию без API ЧЗ; API ЧЗ + УКЭП — только для SSCC-коробов
    при включённом режиме в настройках.
    """
    supply = "supply"
    demand = "demand"
    loss = "loss"
    move = "move"
    salesreturn = "salesreturn"


class ScanStatus(str, PyEnum):
    pending = "pending"
    scanned = "scanned"  # формат GS1 локально валиден, но КМ ещё не проверена в ЧЗ (пакетная проверка по кнопке)
    valid = "valid"
    invalid = "invalid"
    duplicate = "duplicate"
    overflow = "overflow"  # сверх плана: визуально красный, но идёт в отгрузку
    unknown_product = "unknown_product"  # КМ валидна, но GTIN не найден ни в плане, ни в каталоге МС — нужно ручное сопоставление
    used_in_other_doc = "used_in_other_doc"  # код уже есть в другом документе того же типа — предупреждение, в МС не уходит


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    integration = relationship("Integration", back_populates="user", uselist=False)
    documents = relationship("Document", back_populates="user")
    cz_logs = relationship("CzLog", back_populates="user")


class Integration(Base):
    __tablename__ = "integrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    moysklad_token = Column(Text, nullable=True)         # encrypted
    moysklad_account_id = Column(String(255), nullable=True)
    moysklad_account_name = Column(String(255), nullable=True)
    cz_token = Column(Text, nullable=True)               # encrypted access_token from /auth/cert/
    cz_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    cz_cert_thumbprint = Column(String(64), nullable=True)
    cz_cert_subject = Column(String(500), nullable=True)
    cz_auth_method = Column(String(16), nullable=False, default="mock", server_default="mock")
    cz_box_mode_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    cz_inn = Column(String(12), nullable=True)           # ИНН участника оборота для тела документов ЧЗ
    # Товарные группы (pg) ЧЗ, которые маркирует этот клиент. Задаёт, какие группы
    # перебирать в True API (cises/info и т.п.). Пустой список → fallback на глобальный
    # settings.CZ_PRODUCT_GROUPS. Сужение перебора = меньше запросов в ЧЗ = быстрее скан.
    cz_product_groups = Column(JSONB, nullable=False, default=list, server_default="[]")
    # ЭДО Saby (СБИС) для раздела «Контроль марок»: логин/аккаунт + пароль (Fernet).
    # Сессия (sid) не хранится в БД — кэшируется в Redis (saby_sid:<user_id>).
    saby_login = Column(String(255), nullable=True)
    saby_password = Column(Text, nullable=True)          # encrypted
    saby_account = Column(String(64), nullable=True)     # НомерАккаунта (опц.)
    # Сервисная авторизация приложения (oauth/service → X-SBISAccessToken) — рекомендованный
    # способ для фоновой интеграции. app_client_id = «id подключения», ключи — Fernet.
    saby_app_client_id = Column(String(128), nullable=True)
    saby_app_secret = Column(Text, nullable=True)        # encrypted («защитный ключ»)
    saby_secret_key = Column(Text, nullable=True)        # encrypted (если требуется)
    # Курсор ленты СБИС.СписокИзменений для инкрементальной синхронизации ЭДО:
    # id/дата последнего обработанного события + id документа (см. project_mark_control_saby).
    saby_last_event_id = Column(String(64), nullable=True)
    saby_last_event_dt = Column(String(32), nullable=True)   # «ДД.ММ.ГГГГ ЧЧ.ММ.СС»
    saby_last_doc_id = Column(String(64), nullable=True)
    saby_synced_at = Column(DateTime(timezone=True), nullable=True)
    # Инвентаризация: карта «наши склады» — id складов МС периметра юрлица, по которым
    # берём учётный остаток для сверки с ЧЗ (мультиюрлицо: остаток МС по складу, а не по
    # юрлицу). Пустой список = все склады. См. ms_stock_snapshot / api/inventory.py.
    inventory_store_ids = Column(JSONB, nullable=False, default=list, server_default="[]")
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="integration")


class OAuthState(Base):
    """Одноразовые state токены для OAuth CSRF защиты."""
    __tablename__ = "oauth_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    moysklad_id = Column(String(255), nullable=True, index=True)
    name = Column(String(500), nullable=False)
    kind = Column(
        Enum(DocumentKind),
        default=DocumentKind.demand,
        server_default=DocumentKind.demand.value,
        nullable=False,
    )
    status = Column(Enum(DocumentStatus), default=DocumentStatus.draft, nullable=False)
    # План сборки: массив объектов {gtin, product_id, product_name, expected_qty}.
    # Пустой массив = режим без плана (произвольная сборка).
    plan = Column(JSONB, nullable=False, default=list, server_default="[]")
    # Списание (loss): код выбранной причины вывода из оборота и id поданных в ЧЗ документов.
    writeoff_reason = Column(String(64), nullable=True)
    cz_doc_ids = Column(JSONB, nullable=True)
    # Приёмка (supply через загрузку УПД): выбранная товарная группа (молоко/табак/…).
    product_group = Column(String(64), nullable=True)
    # Реквизиты из шапки УПД для комментария поступления МС: {invoice_number, invoice_date}.
    upd_meta = Column(JSONB, nullable=True)
    # Причина неуспешной отправки в МС (напр. истёк токен ЧЗ) — показывается на
    # странице приёмки вместо ложного «МойСклад ещё обрабатывает».
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="documents")
    scans = relationship("Scan", back_populates="document", cascade="all, delete-orphan")


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (
        UniqueConstraint("document_id", "code", name="ix_scans_document_code_unique"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    code = Column(Text, nullable=False)
    gtin = Column(String(14), nullable=True)
    serial = Column(String(50), nullable=True)
    # Явный товар МС (UUID), если кладовщик выбрал строку вручную — приоритет при process.
    moysklad_product_id = Column(String(64), nullable=True)
    status = Column(Enum(ScanStatus), default=ScanStatus.pending, nullable=False)
    product_name = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    # Короб SSCC, сохранённый «целиком» (без раскрытия на штучные КМ).
    # is_box=True → code хранит SSCC, box_quantity = число SGTIN внутри (из ЧЗ sscc_check).
    # В МС такой скан пишется одним trackingCode type=transportpack с quantity=box_quantity.
    is_box = Column(Boolean, nullable=False, default=False, server_default="false")
    box_quantity = Column(Integer, nullable=True)
    # Немаркированный товар: скан обычного штрихкода (EAN-13), не КМ. ЧЗ не вызывается,
    # box_quantity хранит накопленное кол-во (скан = +1). В МС пишется только quantity
    # позиции без trackingCode.
    is_barcode = Column(Boolean, nullable=False, default=False, server_default="false")
    # Состав агрегата (блок/групповая упаковка): КМ вложенных пачек из ЧЗ cises/info.
    # Непустой → скан представляет box_quantity единиц и при process разворачивается
    # в эти коды поштучно (is_box=False; в отличие от SSCC-transportpack).
    child_codes = Column(JSONB, nullable=True)
    # Владелец и производитель КМ из ЧЗ (cises/info) — для отображения при проверке.
    owner_name = Column(String(500), nullable=True)
    producer_name = Column(String(500), nullable=True)
    # ИНН владельца марки (ЧЗ ownerInn) — сверка с владельцем подписи (Integration.cz_inn)
    # в отгрузке: несовпадение подсвечивается на фронте, но не блокирует.
    owner_inn = Column(String(12), nullable=True)
    # Марка выведена из оборота / заблокирована (ЧЗ markWithdraw) + причина. Подсветка +
    # предупреждение при отгрузке, но не блокирует (статус скана остаётся прежним).
    withdrawn = Column(Boolean, nullable=False, server_default="false")
    withdraw_reason = Column(String(200), nullable=True)
    scanned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    document = relationship("Document", back_populates="scans")


class GtinProductMap(Base):
    """Запоминание соответствия GTIN → товар МС для приёмки по УПД.

    При ручном сопоставлении несмаппленного GTIN кладовщик выбирает товар —
    связка сохраняется здесь и применяется при следующих загрузках УПД того же
    пользователя (автоматический резолв до похода в каталог МС).
    """
    __tablename__ = "gtin_product_map"
    __table_args__ = (
        UniqueConstraint("user_id", "gtin", name="ix_gtin_product_map_user_gtin"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    gtin = Column(String(14), nullable=False)
    product_id = Column(String(64), nullable=False)
    product_name = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GtinNameMap(Base):
    """База знаний «GTIN → наименование товара» из первичных XML УПД (ЭДО Saby).

    Имена из ЧЗ по кодам баланса вытащить не удалось (dispenser productName пуст,
    cises/info не находит коды без криптохвоста), а Национальный каталог требует
    отдельного доступа. Но каждый исходящий УПД несёт пару НаимТов↔GTIN — при синке
    ЭДО (edo_sync) её сохраняем сюда. Инвентаризация (reconcile) берёт имя фолбэком
    после МС и ЧЗ. Отдельно от GtinProductMap: там product_id (МС) NOT NULL, а УПД
    его не даёт — здесь только имя. Пер-клиент (наименование поставщика/своё).
    """
    __tablename__ = "gtin_name_map"
    __table_args__ = (
        UniqueConstraint("user_id", "gtin", name="ix_gtin_name_map_user_gtin"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    gtin = Column(String(14), nullable=False)
    product_name = Column(String(500), nullable=False)
    source = Column(String(16), nullable=False, default="upd", server_default="upd")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GtinCzGroup(Base):
    """Кэш «GTIN → товарная группа ЧЗ (pg)» — засевается из trackingType карточки МС.

    Товарная группа — стабильное свойство самого GTIN (одинаково для всех клиентов),
    поэтому таблица ГЛОБАЛЬНАЯ (без user_id). Нужна, чтобы пакетная проверка марок
    (verify_document_task → check_codes) сразу знала правильную pg и не зависела от
    того, включил ли клиент нужную группу галочкой в настройках: раньше при отсутствии
    группы в списке ВСЕ коды получали «КМ/КИ не найден». Резолв: своя БД → МС
    (find_product_by_gtin.trackingType) → запись сюда. Только оптимизация/подсказка —
    при промахе перебор настроенных групп клиента всё равно проходит.
    """
    __tablename__ = "gtin_cz_group"

    gtin = Column(String(14), primary_key=True)
    product_group = Column(String(32), nullable=False)
    # Источник значения: ms (из trackingType карточки), cz (подтверждён ответом ЧЗ).
    source = Column(String(16), nullable=False, default="ms")
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EdoDocument(Base):
    """Документ ЭДО (Saby) для контроля оборота марок — исходящие УПД/реализации.

    Синхронизируется из СБИС.СписокИзменений (курсор в Integration). Марки берём из
    первичного XML-вложения через upd_parser. Пер-клиент, дедуп по (user_id, external_id).
    """
    __tablename__ = "edo_documents"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="ix_edo_documents_user_external"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(16), nullable=False, default="saby", server_default="saby")
    external_id = Column(String(64), nullable=False)     # Документ.Идентификатор
    number = Column(String(64), nullable=True)
    doc_date = Column(String(16), nullable=True)          # «ДД.ММ.ГГГГ» как в Saby
    direction = Column(String(16), nullable=True)         # Исходящий/Входящий
    doc_type = Column(String(64), nullable=True)          # Регламент.Название («Реализация»)
    counterparty_inn = Column(String(12), nullable=True, index=True)
    counterparty_name = Column(String(500), nullable=True)
    state_code = Column(Integer, nullable=True)           # Состояние.Код
    state_name = Column(String(200), nullable=True)
    # Статус документа с маркированным товаром в ГИС МТ (Расширение.СостояниеМарк/ГосСистемы).
    mark_state = Column(JSONB, nullable=True)
    marks_parsed = Column(Boolean, nullable=False, default=False, server_default="false")
    codes_total = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    marks = relationship("EdoMark", back_populates="document", cascade="all, delete-orphan")


class EdoMark(Base):
    """Код маркировки из исходящего УПД ЭДО. cis_canonical — для сверки с остатком ЧЗ."""
    __tablename__ = "edo_marks"
    __table_args__ = (
        UniqueConstraint("document_id", "cis_canonical", name="ix_edo_marks_doc_cis"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("edo_documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    cis_raw = Column(Text, nullable=False)
    cis_canonical = Column(String(60), nullable=False, index=True)
    gtin = Column(String(14), nullable=True)

    document = relationship("EdoDocument", back_populates="marks")


class CzOwnerMark(Base):
    """Снимок остатка марок в ЧЗ (что числится за участником) — для сверки с ЭДО.

    Заполняется выгрузкой dispenser (FILTERED_CIS_REPORT). Пер-клиент, ключ —
    канонический CIS. Сверка: edo_marks ∩ cz_owner_marks по (user_id, cis_canonical)."""
    __tablename__ = "cz_owner_marks"
    __table_args__ = (
        UniqueConstraint("user_id", "cis_canonical", name="ix_cz_owner_marks_user_cis"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    cis_canonical = Column(String(60), nullable=False, index=True)
    gtin = Column(String(14), nullable=True)
    product_name = Column(String(500), nullable=True)   # наименование из ЧЗ (dispenser productName)
    status = Column(String(32), nullable=True)
    package_type = Column(String(16), nullable=True)
    product_group = Column(String(32), nullable=True)
    snapshot_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class MsStockSnapshot(Base):
    """Снимок учётного остатка МойСклада по маркированным товарам — для сверки с ЧЗ.

    Заполняется фоново (app/services/ms_stock.refresh_ms_stock) по «нашим складам»
    (Integration.inventory_store_ids). Только товары, чей GTIN присутствует в снимке ЧЗ
    (cz_owner_marks). Сверка: cz_owner_marks (агрегат по gtin) ↔ ms_stock_snapshot по
    (user_id, gtin). folder_* = группа товаров МС = «бренд» для среза инвентаризации."""
    __tablename__ = "ms_stock_snapshot"
    __table_args__ = (
        UniqueConstraint("user_id", "gtin", name="ix_ms_stock_snapshot_user_gtin"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(String(64), nullable=False)      # МС product id
    gtin = Column(String(14), nullable=False, index=True)
    product_name = Column(String(500), nullable=True)
    folder_id = Column(String(64), nullable=True)         # группа товаров МС (productFolder id)
    folder_name = Column(String(500), nullable=True)      # «бренд» = имя/путь группы
    qty = Column(Numeric, nullable=False, default=0, server_default="0")
    store_breakdown = Column(JSONB, nullable=True)        # {склад: кол-во}
    snapshot_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class CzLog(Base):
    """Все запросы в Честный Знак — критично для отладки."""
    __tablename__ = "cz_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    request_method = Column(String(10), nullable=False)
    request_url = Column(Text, nullable=False)
    request_body = Column(JSON, nullable=True)
    response_status = Column(Integer, nullable=True)
    response_body = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="cz_logs")
