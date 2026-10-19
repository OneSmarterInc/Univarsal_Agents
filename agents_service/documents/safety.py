"""Safety checks on every upload, before it is stored.

The content is checked, not only the file name: a PDF must start like a PDF,
a Word file must be a clean Word archive, an image must open as that image.
"""

import io
import warnings
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

from agents_service.core.errors import ServiceError

MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
GENERIC_TYPES = ('', 'application/octet-stream')


def check_docx(raw):
    """A Word archive without zip bombs, macros, encryption or outside links."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 2000
                or sum(i.file_size for i in entries) > 50 * 1024 * 1024
                or any(i.file_size > max(1, i.compress_size) * 100 for i in entries)
            ):
                raise ServiceError('decompression_limit')
            names = {i.filename for i in entries}
            if 'word/document.xml' not in names or '[Content_Types].xml' not in names:
                raise ServiceError('invalid_document')
            for item in entries:
                parts = PurePosixPath(item.filename).parts
                if (
                    item.flag_bits & 1
                    or item.filename.startswith('/')
                    or '..' in parts
                    or 'vbaproject' in item.filename.lower()
                ):
                    raise ServiceError('invalid_document')
                if item.filename.endswith('.rels'):
                    from defusedxml.ElementTree import fromstring

                    for relation in fromstring(archive.read(item)):
                        external = relation.attrib.get('TargetMode') == 'External'
                        link = relation.attrib.get('Type', '').endswith('/hyperlink')
                        if external and not link:
                            raise ServiceError('external_document_relationship')
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - any archive problem is an invalid document
        raise ServiceError('invalid_document') from exc


def check_pdf(raw):
    if not raw.lstrip()[:5] == b'%PDF-':
        raise ServiceError('invalid_document')
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ServiceError('invalid_document')
        len(reader.pages)
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - a broken PDF is an invalid document
        raise ServiceError('invalid_document') from exc


def check_image(raw, expected):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in expected:
                    raise ServiceError('unsupported_media_type', 415)
                if image.width * image.height > MAX_PIXELS or max(image.size) > 20_000:
                    raise ServiceError('image_pixel_limit')
                image.verify()
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - any decoder problem is an invalid image
        raise ServiceError('invalid_image') from exc


IMAGE_FORMATS = {'.png': ('PNG',), '.jpg': ('JPEG', 'MPO'), '.jpeg': ('JPEG', 'MPO'), '.webp': ('WEBP',)}


def check_file(raw, filename, media_type, rule):
    """Check one upload against an agent's FileRule; returns its file ending."""
    if rule is None:
        raise ServiceError('unsupported_media_type', 415)
    if not raw:
        raise ServiceError('empty_file')
    if len(raw) > rule.max_bytes:
        raise ServiceError('file_too_large', 413)
    suffix = Path(filename or '').suffix.lower()
    media_type = (media_type or '').split(';')[0].strip().lower()
    if suffix not in rule.types or (media_type not in rule.types[suffix] and media_type not in GENERIC_TYPES):
        raise ServiceError('unsupported_media_type', 415)
    if suffix == '.pdf':
        check_pdf(raw)
    elif suffix == '.docx':
        check_docx(raw)
    elif suffix in IMAGE_FORMATS:
        check_image(raw, IMAGE_FORMATS[suffix])
    return suffix
