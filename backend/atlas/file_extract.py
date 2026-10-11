"""Изолированный локальный разбор документов. Никогда не исполняет содержимое."""
import io
import base64
import json
import sys
import zipfile
import xml.etree.ElementTree as ET

MAX_TEXT = 100000
MAX_DOCUMENT = 5 * 1024 * 1024


def extract(raw, extension):
    warnings = []
    if extension in {'png', 'jpg', 'jpeg', 'webp'}:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 16000000
        with Image.open(io.BytesIO(raw)) as source:
            expected = {'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG', 'webp': 'WEBP'}[extension]
            if source.format != expected:
                raise ValueError('Содержимое изображения не соответствует расширению файла')
            if source.width * source.height > 16000000:
                raise ValueError('Изображение больше 16 мегапикселей; уменьшите его')
            if getattr(source, 'n_frames', 1) > 1:
                warnings.append('Анализируется только первый кадр анимации')
            source.load()
            image = ImageOps.exif_transpose(source).convert('RGBA')
            size = image.size
            image.thumbnail((2048, 2048))
            white = Image.new('RGB', image.size, 'white')
            white.paste(image, mask=image.getchannel('A'))
            out = io.BytesIO(); white.save(out, format='JPEG', quality=90)
            if out.tell() > 1024 * 1024:
                white.thumbnail((1536, 1536)); out = io.BytesIO(); white.save(out, format='JPEG', quality=75)
            if out.tell() > 1024 * 1024:
                raise ValueError('Изображение слишком сложное; уменьшите его')
            warnings.append('Модели передаётся изображение до 2048 px без EXIF; мелкие детали могут потеряться')
            return {'text': '', 'image': base64.b64encode(out.getvalue()).decode(),
                    'width': size[0], 'height': size[1], 'warnings': warnings}
    elif extension == 'pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ValueError('PDF защищён паролем. Загрузите незашифрованную копию')
        if len(reader.pages) > 50:
            raise ValueError('Допустимо до 50 страниц PDF')
        parts = []
        stream_bytes = 0
        empty = 0
        for page in reader.pages:
            stream = page.get_contents()
            if stream:
                stream_bytes += len(stream.get_data())
                if stream_bytes > 10 * 1024 * 1024:
                    raise ValueError('PDF слишком сложный для безопасного разбора')
            value = page.extract_text() or ''
            empty += not value.strip()
            parts.append(value)
            if sum(len(p.encode()) for p in parts) > MAX_TEXT:
                raise ValueError('В документе больше 100 КБ текста; разделите его на части')
        text = '\n\n'.join(parts)
        warnings.append('Читается только текст; изображения, оформление и таблицы могут быть утрачены')
        if empty:
            warnings.append(f'Без извлекаемого текста: {empty} стр. Сканы не распознаются')
    elif extension == 'docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(i.file_size for i in entries) > 10 * 1024 * 1024:
                raise ValueError('DOCX слишком большой после распаковки')
            if any(i.file_size > max(i.compress_size, 1) * 200 for i in entries):
                raise ValueError('DOCX имеет небезопасную степень сжатия')
            if any('vbaproject' in i.filename.lower() for i in entries):
                raise ValueError('Документы с макросами не поддерживаются')
            xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError('Небезопасная структура DOCX')
            root = ET.fromstring(xml)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(p.itertext()) for p in root.iter(ns + 'p'))
        warnings.append('Читается основной текст; изображения, колонтитулы и оформление не анализируются')
    else:
        text = raw.decode('utf-8-sig')
    if not text.strip():
        raise ValueError('Нет доступного текста. Для сканов и изображений нужен OCR, он пока не подключён')
    if len(text.encode()) > MAX_TEXT:
        raise ValueError('В документе больше 100 КБ текста; разделите его на части')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError('Бинарные данные не поддерживаются')
    return {'text': text, 'warnings': warnings}


if __name__ == '__main__':
    try:
        if sys.platform != 'win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        raw = sys.stdin.buffer.read(MAX_DOCUMENT + 1)
        if len(raw) > MAX_DOCUMENT:
            raise ValueError('Документ превышает 5 МиБ')
        result = extract(raw, sys.argv[1])
    except ValueError as error:
        result = {'error': str(error)}
    except Exception:
        result = {'error': 'Не удалось прочитать документ. Проверьте формат и отсутствие повреждений'}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
