"""Bounded, secret-free evidence shared by one document operation."""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

_SECRET_KEYS = {'authorization', 'password', 'token', 'accesstoken', 'refreshtoken',
                'secret', 'secretkey', 'apikey', 'signeddata', 'signature', 'cookie'}


def secret_key(key):
    normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
    return normalized in _SECRET_KEYS or any(normalized.endswith(k) for k in _SECRET_KEYS)


def safe_text(value):
    value = str(value)
    value = re.sub(r'(?i)Bearer\s+[^\s\"\']+', 'Bearer <redacted>', value)
    value = re.sub(r'(?i)((?:api[_-]?key|[\w_-]*token|[\w_-]*secret|password|signed_data)=)[^&\s\"\']+',
                   r'\1<redacted>', value)
    value = re.sub(r'(?i)([\"\'](?:access[_-]?token|refresh[_-]?token|token|api[_-]?key|password|signed_data)[\"\']\s*:\s*[\"\'])[^\"\']+',
                   r'\1<redacted>', value)
    value = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '<redacted>', value)
    return value[:2000]


def safe_url(url):
    parts = urlsplit(str(url))
    host = parts.netloc.rsplit('@', 1)[-1]
    query = [(k, '<redacted>' if secret_key(k) else safe_text(v)) for k, v in parse_qsl(parts.query)]
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ''))


def sanitize(value, depth=0):
    if depth > 7:
        return '<truncated>'
    if isinstance(value, dict):
        return {str(k): '<redacted>' if secret_key(k) else sanitize(v, depth + 1)
                for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        items = [sanitize(v, depth + 1) for v in value[:20]]
        if len(value) > 20:
            items.append({'truncated_items': len(value) - 20})
        return items
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return safe_text(value)


@dataclass
class Operation:
    document_id: str
    user_id: str
    stage: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    requests: list = field(default_factory=list)
    code: str | None = None
    dropped_requests: int = 0


current_operation = ContextVar('document_diagnostics', default=None)


@contextmanager
def operation_scope(document_id, user_id, stage):
    operation = Operation(str(document_id), str(user_id), stage)
    token = current_operation.set(operation)
    try:
        yield operation
    finally:
        current_operation.reset(token)


def capture_request(system, method, url, request, status=None, response=None, error=None, *, original_code=None):
    operation = current_operation.get()
    if operation is None:
        return
    item = sanitize({'system': system, 'stage': operation.stage, 'method': method,
                     'url': safe_url(url), 'original_code': original_code or operation.code,
                     'request': request, 'status': status, 'response': response, 'error': error})
    # Keep failures preferentially when a document has thousands of successful requests.
    if len(operation.requests) >= 30:
        index = next((i for i, v in enumerate(operation.requests)
                      if not v.get('error') and (v.get('status') or 0) < 400), 0)
        operation.requests.pop(index)
        operation.dropped_requests += 1
    operation.requests.append(item)


def response_body(response):
    try:
        return response.json()
    except ValueError:
        return {'text': safe_text(response.text)}


def failure_reason(exc):
    """Group the same failure without merging every ValueError in a document."""
    message = safe_text(exc)
    message = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\d{6,}', '<id>', message, flags=re.I)
    return f'{type(exc).__name__}:{message[:110]}'
