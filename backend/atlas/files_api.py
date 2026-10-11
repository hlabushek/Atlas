"""Только authenticated owner API; загрузка никогда не вызывает модель."""
import asyncio
import hashlib
from urllib.parse import quote
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from .api import DB, Secure
from .models import Attachment, Audit, Message, now
from .files import TYPES, MAX_DOCUMENT, MAX_TEXT_FILE, MAX_CONTEXT, filename, parse, metadata

router = APIRouter(prefix='/api')


@router.get('/files/capabilities')
def capabilities(s: DB, _: Secure):
    from .file_models import roles
    return {'extensions': list(TYPES), 'max_files': 5, 'document_bytes': MAX_DOCUMENT,
            'text_file_bytes': MAX_TEXT_FILE, 'context_bytes': MAX_CONTEXT, 'ocr': False, 'vision': True, 'roles': roles(s)}


@router.get('/files')
def files(s: DB, _: Secure):
    return [metadata(a) for a in s.scalars(select(Attachment).where(Attachment.deleted_at.is_(None)).order_by(Attachment.created_at.desc()))]


@router.post('/files')
async def upload(s: DB, _: Secure, file: UploadFile = File(...)):
    try:
        name = filename(file.filename)
        raw = await file.read(MAX_DOCUMENT + 1)
    finally:
        await file.close()
    text, mime, warnings, image, width, height = await asyncio.to_thread(parse, raw, name)
    a = Attachment(name=name, content=text, original=raw, mime=mime, warnings=warnings, image=image, width=width, height=height, sha256=hashlib.sha256(raw).hexdigest())
    s.add(a); s.flush()
    s.add(Audit(event='attachment_uploaded', details={'id': a.id, 'bytes': len(raw), 'mime': mime}))
    s.commit()
    return metadata(a)


def visible(s, identifier, message_id=None):
    a = s.get(Attachment, identifier)
    if message_id:
        m = s.get(Message, message_id)
        if m is None or identifier not in {v['id'] for v in (m.attachments or [])}:
            raise HTTPException(404, 'Вложение сообщения не найдено')
    elif a is not None and a.deleted_at is not None:
        a = None
    if a is None:
        raise HTTPException(404, 'Файл не найден')
    return a


@router.get('/files/{identifier}/preview')
def preview(identifier: str, s: DB, _: Secure, message_id: str | None = None):
    a = visible(s, identifier, message_id)
    return dict(metadata(a), text=a.content)


@router.get('/files/{identifier}/image')
def image(identifier: str, s: DB, _: Secure, message_id: str | None = None):
    a = visible(s, identifier, message_id)
    if not a.image:
        raise HTTPException(404, 'Изображение не найдено')
    return Response(a.image, media_type='image/jpeg', headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


@router.get('/files/{identifier}')
def download(identifier: str, s: DB, _: Secure, message_id: str | None = None):
    a = visible(s, identifier, message_id)
    return Response(a.original if a.original is not None else a.content.encode(), media_type=a.mime or 'text/plain',
                    headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
                             'Content-Disposition': "attachment; filename*=UTF-8''" + quote(a.name, safe='')})


@router.delete('/files/{identifier}')
def remove(identifier: str, s: DB, _: Secure):
    a = visible(s, identifier)
    a.deleted_at = now()
    s.add(Audit(event='attachment_hidden', details={'id': a.id}))
    s.commit()
    return {'ok': True, 'retained_in_messages': True}
