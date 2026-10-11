"""Bounded RouterAI speech transcription with a separate RUB budget ledger."""
import base64
from datetime import datetime, timezone
import httpx
from sqlalchemy import select, func, update
from .config import settings
from .db import sessions
from .model_settings import credential
from .models import Budget, SpeechUsage

PROVIDER = "routerai:assemblyai/universal-3-5-pro"

def estimated_kopecks(seconds: int, rate: int) -> int:
    # Round up each partial kopeck so reservations never understate this configured rate.
    return (seconds * rate + 59) // 60

def reserve(seconds: int) -> str:
    cfg = settings()
    cost = estimated_kopecks(seconds, cfg.telegram_stt_kopecks_per_minute)
    with sessions()() as s:
        s.execute(update(Budget).where(Budget.id == 1).values(token_limit=Budget.token_limit))
        b = s.get(Budget, 1)
        stamp = datetime.now(timezone.utc)
        month = stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        month_used = s.scalar(select(func.coalesce(func.sum(SpeechUsage.cost_kopecks), 0)).where(SpeechUsage.created_at >= month))
        day_used = s.scalar(select(func.coalesce(func.sum(SpeechUsage.cost_kopecks), 0)).where(SpeechUsage.created_at >= day))
        if b.speech_rub_month_limit is not None and month_used + cost > b.speech_rub_month_limit:
            raise ValueError("Достигнут месячный лимит распознавания речи в рублях")
        if b.speech_rub_day_limit is not None and day_used + cost > b.speech_rub_day_limit:
            raise ValueError("Достигнут дневной лимит распознавания речи в рублях")
        row = SpeechUsage(provider=PROVIDER, status="RESERVED", duration_seconds=seconds, cost_kopecks=cost)
        s.add(row); s.commit(); return row.id

def settle(identifier: str, known: bool):
    with sessions()() as s:
        row = s.get(SpeechUsage, identifier)
        if row and row.status == "RESERVED":
            row.status = "KNOWN" if known else "UNKNOWN"
            s.commit()

def transcribe(raw: bytes, duration: int) -> str:
    cfg = settings()
    key = credential("routerai")
    if not key:
        raise ValueError("Для распознавания речи настройте ATLAS_ROUTERAI_API_KEY")
    reservation = reserve(duration)
    try:
        response = httpx.post(
            "https://routerai.ru/api/v1/audio/transcriptions",
            headers={"Authorization": "Bearer " + key},
            json={"model": cfg.telegram_stt_model,
                  "input_audio": {"data": base64.b64encode(raw).decode("ascii"), "format": "ogg"},
                  "language": "ru"},
            timeout=90,
            follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError("Провайдер вернул пустую или слишком длинную расшифровку")
        settle(reservation, True)
        return text.strip()
    except Exception:
        settle(reservation, False)
        raise
