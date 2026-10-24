"""Safety checks on every upload, before it is stored.

The content is checked, not only the file name: a PDF must start like a PDF,
a Word file must be a clean Word archive, an image must open as that image.

Only quick checks run here, in the API process: they read sizes and headers,
never a whole document. The slow checks (a PDF's page tree, a Word file's
links) run in the worker's limited reader process (``deep_check_docx`` and
``documents.reading``), so a hostile file cannot slow down or crash the API.
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
# Real .rels parts are a few kilobytes; a large one is an attack on the parser.
MAX_RELS_BYTES = 1024 * 1024


def check_docx(raw):
    """A Word archive without zip bombs, macros, encryption or path tricks.

    Reads only the archive's list of parts; no XML is parsed here.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
    except Exception as exc:  # noqa: BLE001 - any archive problem is an invalid document
        raise ServiceError('invalid_document') from exc
    if (
        len(entries) > 2000
        or sum(i.file_size for i in entries) > 50 * 1024 * 1024
        or any(i.file_size > max(1, i.compress_size) * 100 for i in entries)
        or any(i.filename.endswith('.rels') and i.file_size > MAX_RELS_BYTES for i in entries)
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


def deep_check_docx(archive):
    """Refuse a Word file that links to outside content (hyperlinks are fine).

    Parses every .rels part, so it runs in the limited reader process only.
    Returns an error code, or None when the file is clean.
    """
    from defusedxml.ElementTree import fromstring

    for item in archive.infolist():
        if not item.filename.endswith('.rels'):
            continue
        if item.file_size > MAX_RELS_BYTES:
            return 'decompression_limit'
        for relation in fromstring(archive.read(item)):
            external = relation.attrib.get('TargetMode') == 'External'
            link = relation.attrib.get('Type', '').endswith('/hyperlink')
            if external and not link:
                return 'external_document_relationship'
    return None


def check_pdf(raw):
    """A PDF header. The page tree and encryption are checked by the reader process."""
    if not raw.lstrip()[:5] == b'%PDF-':
        raise ServiceError('invalid_document')


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
