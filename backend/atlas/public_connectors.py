"""Bounded polling of owner-approved public HTML pages."""
from datetime import timedelta
import json
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
import soupsieve
from bs4 import BeautifulSoup
from sqlalchemy import select

from .db import sessions
from .models import Audit, ConnectorSource, now
from .network import endpoint
from .integration_permissions import schedule_valid
from .connector_sessions import headers as session_headers

MAX_HTML_BYTES = 1_000_000


def _visible(element):
    for parent in [element, *element.parents]:
        if getattr(parent, "name", None) in {"script", "style", "template", "input", "textarea", "select"}:
            return False
        if getattr(parent, "attrs", {}).get("hidden") is not None:
            return False
        style = str(getattr(parent, "attrs", {}).get("style", "")).replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return False
    return True


def extract(html: bytes, page_url: str, recipe: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = [row for row in soup.select(recipe["rows"]) if _visible(row)][:500]
    if not rows:
        raise ValueError("Рецепт не нашёл записей в HTML страницы")
    records = []
    for row in rows:
        record = {}
        for name, selector in recipe["fields"].items():
            element = row if soupsieve.match(selector, row) else row.select_one(selector)
            if element is None or not _visible(element):
                continue
            if name == "image_url":
                if element.name != "img":
                    continue
                if not element.get("src"):
                    continue
                value = urljoin(page_url, element["src"])
                parsed = urlparse(value)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    continue
            else:
                value = element.get("datetime") or element.get("aria-label") or element.get("title") or element.get_text(" ", strip=True)
                value = " ".join(str(value).split())
            if value:
                record[name] = value[:1000]
        if record.get(recipe["key"]) and record.get("title"):
            records.append(record)
    if not records or len({r[recipe["key"]] for r in records}) != len(records):
        raise ValueError("Не найдены записи с уникальными ключами")
    return records


def extract_json(raw: bytes, page_url: str, recipe: dict) -> list[dict]:
    document = json.loads(raw)
    if not isinstance(document, dict) or not isinstance(document.get(recipe["collection"]), list):
        raise ValueError("В JSON не найдена выбранная коллекция")
    records = []
    for parent in document[recipe["collection"]]:
        if not isinstance(parent, dict) or not isinstance(parent.get(recipe["items"]), list):
            raise ValueError("В JSON не найден список элементов")
        for item in parent[recipe["items"]]:
            if not isinstance(item, dict):
                raise ValueError("Элемент JSON должен быть объектом")
            record = {}
            for name, path in recipe["fields"].items():
                value = parent.get(path[8:]) if path.startswith("@parent.") else item.get(path)
                if value is None or isinstance(value, (dict, list, bool)):
                    continue
                value = str(value).strip()
                if name == "image_url" and value:
                    value = urljoin(page_url, value)
                    parsed = urlparse(value)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                        continue
                if value:
                    record[name] = value[:1000]
            if not record.get("title") and record.get("image_url"):
                filename = PurePosixPath(urlparse(record["image_url"]).path).stem
                record["title"] = f"{parent.get('ru') or parent.get('id') or 'Фото'} · {filename}"[:1000]
            if record.get(recipe["key"]) and record.get("title"):
                records.append(record)
            if len(records) > 500:
                raise ValueError("JSON содержит больше 500 записей; разделите источник")
    if not records or len({r[recipe["key"]] for r in records}) != len(records):
        raise ValueError("Не найдены записи с уникальными ключами")
    return records


def fetch_public(source: ConnectorSource) -> list[dict]:
    url = endpoint(source.origin)
    if source.fetch_mode == 'browser':
        from .browser_connectors import fetch
        return fetch(source)
    is_json = source.recipe.get("kind") == "public_json"
    request_headers={"User-Agent":"Atlas-source/1.0",**session_headers(source)}
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False, headers=request_headers) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            expected = "application/json" if is_json else "text/html"
            if response.status_code != 200 or expected not in response.headers.get("content-type", "").lower():
                raise ValueError("Источник не вернул ожидаемый формат")
            chunks = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_HTML_BYTES:
                    raise ValueError("HTML страница превышает 1 МБ")
                chunks.append(chunk)
    payload = b"".join(chunks)
    return extract_json(payload, url, source.recipe) if is_json else extract(payload, url, source.recipe)


def tick(stamp=None):
    stamp = stamp or now()
    completed = 0
    for _ in range(2):
        with sessions()() as session:
            source = session.scalar(select(ConnectorSource).where(
                ConnectorSource.enabled.is_(True), ConnectorSource.schedule_minutes.is_not(None),
                ConnectorSource.next_run_at <= stamp).order_by(ConnectorSource.next_run_at).with_for_update(skip_locked=True))
            if source is None:
                break
            if not schedule_valid(source,stamp):
                source.schedule_minutes = None
                source.next_run_at = None
                source.schedule_fingerprint = None
                source.schedule_expires_at = None
                source.last_run_status = "REVOKED"
                session.add(Audit(event="connector_schedule_revoked",details={"connector_id":source.id}))
                session.commit()
                continue
            fingerprint = source.schedule_fingerprint
            source.next_run_at = stamp + timedelta(minutes=source.schedule_minutes)
            source.last_run_status = "RUNNING"
            identifier = source.id
            session.commit()
        try:
            with sessions()() as session:
                source = session.get(ConnectorSource, identifier)
                if source is None or not schedule_valid(source) or source.schedule_fingerprint != fingerprint:
                    continue
                records = fetch_public(source)
            with sessions()() as session:
                source = session.scalar(select(ConnectorSource).where(ConnectorSource.id == identifier).with_for_update())
                if source is None or not schedule_valid(source) or source.schedule_fingerprint != fingerprint:
                    continue
                from .connectors import ImportRecords, import_records
                result = import_records(identifier, ImportRecords(records=records), session, None)
                source.last_run_at = now()
                source.last_run_status = "OK"
                session.add(Audit(event="connector_public_poll", details={"connector_id": identifier, **result}))
                session.commit()
                completed += 1
        except Exception:
            with sessions()() as session:
                source = session.get(ConnectorSource, identifier)
                if source is not None:
                    source.last_run_at = now()
                    source.last_run_status = "FAILED"
                    session.add(Audit(event="connector_public_poll_failed", details={"connector_id": identifier}))
                    session.commit()
    return completed
