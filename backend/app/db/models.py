import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, DateTime, Boolean, Integer, Text, Enum,
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
    valid = "valid"
    invalid = "invalid"
    duplicate = "duplicate"
    overflow = "overflow"  # сверх плана: визуально красный, но идёт в отгрузку


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
        default=DocumentKind.supply,
        server_default=DocumentKind.supply.value,
        nullable=False,
    )
    status = Column(Enum(DocumentStatus), default=DocumentStatus.draft, nullable=False)
    # План сборки: массив объектов {gtin, product_id, product_name, expected_qty}.
    # Пустой массив = режим без плана (произвольная сборка).
    plan = Column(JSONB, nullable=False, default=list, server_default="[]")
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
    status = Column(Enum(ScanStatus), default=ScanStatus.pending, nullable=False)
    product_name = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    scanned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    document = relationship("Document", back_populates="scans")


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
