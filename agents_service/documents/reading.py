"""Turn an uploaded file into plain text: Word (DOCX), PDF (two text readers plus OCR for sparse pages) and screenshots (OCR of the main column)."""

import logging
import re
import zipfile
from pathlib import Path

from defusedxml import ElementTree
from PIL import Image, ImageOps

from agents_service.documents.matching import canonical
from agents_service.documents.text import tidy_text

PDF_MAX_PAGES = 30
# Set by the worker from AGENTS_TESSERACT_CMD (needed on Windows).
TESSERACT_CMD = ''


class ExtractionError(Exception):
    """A safe, actionable message that may be shown to the candidate."""


def public_message(code):
    from agents_service.core.errors import MESSAGES

    return MESSAGES.get(code, MESSAGES['invalid_document'])


def main_column_text(data, width):
    """Text of the main column only, from tesseract word boxes.

    A desktop LinkedIn page has a right sidebar ("People also viewed").
    Tesseract reads it first and joins its lines to the profile's own lines
    ("Hansa Vahini v Jane Doe · 2nd"). When a clear vertical gap separates a
    right-hand column, its words are dropped. Returns None when there is no
    such gap (phone screenshots, documents), so the caller keeps plain OCR.
    """
    words = [
        (
            data['left'][i],
            data['top'][i],
            data['width'][i],
            data['height'][i],
            (data['block_num'][i], data['par_num'][i], data['line_num'][i]),
            data['text'][i].strip(),
        )
        for i in range(len(data['text']))
        if data['text'][i].strip()
    ]
    if len(words) < 30:
        return None
    best = None
    for x in range(int(width * 0.55), int(width * 0.85), 2):
        if any(left <= x <= left + w for left, _, w, _, _, _ in words):
            continue
        right = sum(1 for left, *_ in words if left > x)
        if right >= 8 and right <= len(words) * 0.5:
            best = x
            break
    if best is None:
        return None
    lines = {}
    for left, top, w, h, key, text in words:
        if left + w / 2 < best:
            line = lines.setdefault(key, {'top': top, 'height': h, 'words': []})
            line['top'] = min(line['top'], top)
            line['height'] = max(line['height'], h)
            line['words'].append((left, text))
    ordered = sorted(lines.values(), key=lambda line: line['top'])
    heights = sorted(line['height'] for line in ordered) or [10]
    gap = heights[len(heights) // 2] * 1.8
    out, previous = [], None
    for line in ordered:
        if previous is not None and line['top'] - previous > gap:
            out.append('')
        out.append(' '.join(text for _, text in sorted(line['words'])))
        previous = line['top']
    return '\n'.join(out)


def ocr(image, columns=False):
    import pytesseract

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((3000, 3000))
    try:
        if columns:
            data = pytesseract.image_to_data(
                image, config='--psm 3', timeout=45, output_type=pytesseract.Output.DICT
            )
            main = main_column_text(data, image.width)
            if main and len(main.strip()) >= 30:
                return main
        text = pytesseract.image_to_string(image, config='--psm 3', timeout=45)
        if len(text.strip()) < 30:
            text = pytesseract.image_to_string(image, config='--psm 11', timeout=45)
        return text
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractionError('Image reading is not configured. Please contact support.') from exc


MC_FALLBACK = '{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback'


def _own(element, ns, tag):
    """Descendants with `tag`, not entering text boxes or compatibility
    fallbacks (Word stores each text box twice: the shape and a fallback)."""
    for child in element:
        if child.tag in (MC_FALLBACK, ns + 'txbxContent'):
            continue
        if child.tag == tag:
            yield child
        yield from _own(child, ns, tag)


def docx_paragraphs(root, ns):
    """Every paragraph once, in reading order: a paragraph, then the text-box
    paragraphs anchored in it. Fallback copies are skipped."""

    def walk(element):
        for child in element:
            if child.tag == MC_FALLBACK:
                continue
            if child.tag == ns + 'p':
                yield child
                for box in child.iter(ns + 'txbxContent'):
                    if box in skipped:
                        continue
                    skipped.add(box)
                    yield from walk(box)
            else:
                yield from walk(child)

    fallback_boxes = {box for fb in root.iter(MC_FALLBACK) for box in fb.iter(ns + 'txbxContent')}
    skipped = set(fallback_boxes)
    yield from walk(root)


def docx_paragraph_text(paragraph, ns):
    """Paragraph text with Word line breaks and tabs kept as separators."""
    # Only run children: w:tab inside paragraph properties is a tab stop, not text.
    parts = []
    for run in _own(paragraph, ns, ns + 'r'):
        for node in run:
            if node.tag == ns + 't' and node.text:
                parts.append(node.text)
            elif node.tag in (ns + 'br', ns + 'cr'):
                parts.append('\n')
            elif node.tag == ns + 'tab':
                parts.append('\t')
    return ''.join(parts)


# A lone letter before a lower-case word: "D eveloped", "Classi f ication".
# "a" and "I" are real words, so they do not count.
SPLIT_WORD = re.compile(r'(?<![\w.@/])[b-hj-zB-HJ-Z] (?=[a-z])|(?<=[a-z]) [b-hj-z](?= [a-z])')


def split_words(text):
    """How many words a PDF text layer split into pieces."""
    return len(SPLIT_WORD.findall(text))


DASHES = '-‐‑‒–—'


def restore_dashes(text, other):
    """Put back dashes one text layer dropped and the other kept.

    PDFium can read "2023–2024" as "2023 2024" for some fonts. The two layers
    are aligned without spaces; only dash characters are inserted, each where
    the text has a space or no gap. Nothing else is taken from `other`.
    """
    from difflib import SequenceMatcher

    positions = [i for i, char in enumerate(text) if not char.isspace()]
    mine = ''.join(text[i] for i in positions)
    theirs = ''.join(char for char in other if not char.isspace())
    if not mine or not theirs or len(theirs) > 60000:
        return text
    inserts = {}
    for tag, a1, _a2, b1, b2 in SequenceMatcher(None, mine, theirs, autojunk=False).get_opcodes():
        if tag == 'insert' and 0 < a1 < len(mine) and set(theirs[b1:b2]) <= set(DASHES):
            inserts[positions[a1]] = theirs[b1:b2]
    if not inserts:
        return text
    out, last = [], 0
    for at in sorted(inserts):
        gap_start = positions[positions.index(at) - 1] + 1
        gap = text[gap_start:at]
        out.append(text[last:gap_start])
        # "2023 2024" -> "2023–2024", "ROC AUC" -> "ROC-AUC"; an em dash keeps its spaces.
        spaced = gap if gap and '—' in inserts[at] else ''
        out.extend([spaced, inserts[at], spaced])
        last = at
    out.append(text[last:])
    return ''.join(out)


def layout_text(textpage):
    """The page's text in reading order, from where each piece is on the page.

    Some CV templates draw all headings first and all dates last, so the stored
    text order loses which date belongs to which line. Here every text run is
    placed by its position: runs at the same height form one line, left to
    right, and lines go top to bottom. A real second column (a sidebar with
    sentences) is read as a column of its own; short runs on the right, such as
    dates, stay on their line.
    """
    runs = []
    for index in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(index)
        text = (
            textpage.get_text_bounded(left, bottom, right, top).replace('\r', ' ').replace('\n', ' ').strip()
        )
        if text:
            runs.append((left, bottom, right, top, text))
    if not runs:
        return ''
    gutter = _column_gutter(runs)
    if gutter is None:
        return '\n'.join(_lines(runs))
    left_runs = [run for run in runs if run[2] <= gutter]
    right_runs = [run for run in runs if run[2] > gutter]
    return '\n'.join(_lines(left_runs) + _lines(right_runs))


def _lines(runs):
    rows = []
    for run in sorted(runs, key=lambda r: -(r[1] + r[3]) / 2):
        middle, height = (run[1] + run[3]) / 2, max(run[3] - run[1], 1)
        row = rows[-1] if rows else None
        if row and abs(row['middle'] - middle) <= 0.5 * max(height, row['height']):
            row['runs'].append(run)
        else:
            rows.append({'middle': middle, 'height': height, 'runs': [run]})
    lines = []
    for row in rows:
        parts, last_right = [], None
        for left, _bottom, right, _top, text in sorted(row['runs'], key=lambda r: r[0]):
            gap = '' if last_right is None else (' ' if left - last_right < 15 else '   ')
            parts.append(gap + text)
            last_right = right
        lines.append(''.join(parts))
    return lines


def _column_gutter(runs):
    """The x where a real second column starts, or None.

    A column is real when, on many lines, text sits on both sides of one empty
    vertical strip and the right side holds sentences rather than dates.
    """
    page_left = min(r[0] for r in runs)
    page_right = max(r[2] for r in runs)
    width = page_right - page_left
    if width < 200:
        return None
    best = None
    for step in range(30, 71, 5):
        gutter = page_left + width * step / 100
        if any(r[0] < gutter < r[2] for r in runs):
            continue  # a run crosses it: not an empty strip
        left = [r for r in runs if r[2] <= gutter]
        right = [r for r in runs if r[0] >= gutter]
        if len(left) < 4 or len(right) < 4:
            continue
        average_right = sum(len(r[4]) for r in right) / len(right)
        if average_right < 25:
            continue  # dates, page numbers: they stay on their line
        best = gutter
        break
    return best


def best_pdf_text(path, pypdf_pages):
    """Per page, the cleaner of two text layers.

    pypdf can split words ("D eveloped") and join lines ("NAMEB.E.") for some
    fonts. PDFium keeps the visual lines, in reading order by position (see
    layout_text). The page uses whichever has fewer split words, and PDFium
    unless it lost text.
    """
    try:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(path)) as pdf:
            pdfium_pages = []
            for i in range(len(pdf)):
                page = pdf[i]
                textpage = page.get_textpage()
                try:
                    stream = textpage.get_text_bounded().replace('\r\n', '\n').replace('\r', '\n')
                    try:
                        placed = layout_text(textpage)
                    except Exception:  # noqa: BLE001 - the stored order is used instead
                        placed = ''
                    # Reading order by position, unless it lost text compared with the stored order.
                    keep = len(re.sub(r'\s', '', placed)) >= 0.97 * len(re.sub(r'\s', '', stream))
                    pdfium_pages.append(placed if keep and placed else stream)
                finally:
                    textpage.close()
                    page.close()
    except Exception as exc:  # noqa: BLE001 - the pypdf text is used instead
        logging.getLogger(__name__).info('PDFium text skipped: exception_type=%s', type(exc).__name__)
        return pypdf_pages
    chosen = []

    def size(text):
        return len(re.sub(r'\s', '', text))

    for first, second in zip(pypdf_pages, pdfium_pages, strict=False):
        lost_text = size(second) < 0.8 * size(first)
        chosen.append(
            first if lost_text or split_words(second) > split_words(first) else restore_dashes(second, first)
        )
    return chosen + pypdf_pages[len(chosen) :]


WORD_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


MAX_DOCX_TEXT_BYTES = 10 * 1024 * 1024


MIN_TEXT_CHARS = 30


MAX_TEXT_CHARS = 36000


def _docx_parts(archive, root):
    """Word parts to read: referenced headers first, then the body, then footers."""
    import posixpath

    parts, headers = ['word/document.xml'], []
    referenced = {
        node.get(REL_NS + 'id')
        for kind in ('headerReference', 'footerReference')
        for node in root.iter(WORD_NS + kind)
    }
    if not referenced or 'word/_rels/document.xml.rels' not in archive.namelist():
        return parts
    for rel in ElementTree.fromstring(archive.read('word/_rels/document.xml.rels')):
        kind = rel.get('Type', '')
        if rel.get('Id') not in referenced or rel.get('TargetMode') == 'External':
            continue
        if not kind.endswith(('/header', '/footer')):
            continue
        target = rel.get('Target', '')
        part = posixpath.normpath(
            target.lstrip('/') if target.startswith('/') else posixpath.join('word', target)
        )
        if not part.startswith('word/') or part in parts or part in headers or part not in archive.namelist():
            continue
        # Headers go first: a CV may put the owner's name only there.
        (headers if kind.endswith('/header') else parts).append(part)
    return headers + parts


def _docx_text(path):
    # Resolve only local header/footer parts referenced by the document.
    from agents_service.documents.safety import deep_check_docx

    with zipfile.ZipFile(path) as archive:
        problem = deep_check_docx(archive)
        if problem:
            raise ExtractionError(public_message(problem))
        root = ElementTree.fromstring(archive.read('word/document.xml'))
        chunks, total = [], 0
        for part in _docx_parts(archive, root):
            total += archive.getinfo(part).file_size
            if total > MAX_DOCX_TEXT_BYTES:
                raise ExtractionError('The document text exceeds the processing limit.')
            part_root = ElementTree.fromstring(archive.read(part))
            chunk = '\n'.join(docx_paragraph_text(p, WORD_NS) for p in docx_paragraphs(part_root, WORD_NS))
            if chunk and chunk not in chunks:
                chunks.append(chunk)
        return '\n'.join(chunks)


def _with_ocr_lines(text, image_text):
    """Selectable text plus the OCR lines it does not already contain."""
    seen = {canonical(line) for line in text.splitlines() if line.strip()}
    extra = []
    for line in image_text.splitlines():
        key = canonical(line)
        if key and key not in seen:
            extra.append(line)
            seen.add(key)
    return text + '\n' + '\n'.join(extra)


def _ocr_page(pdf, index, text):
    page = pdf[index]
    try:
        width, height = page.get_size()
        bitmap = page.render(scale=min(2, 3000 / max(width, height)))
        try:
            return _with_ocr_lines(text, ocr(bitmap.to_pil()))
        finally:
            bitmap.close()
    finally:
        page.close()


def _pdf_text(path):
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ExtractionError(public_message('invalid_document'))
    # The declared count is read first: a huge page tree is refused before it is walked.
    # The real count is still checked below, because the declared one can be wrong.
    try:
        declared = int(reader.trailer['/Root']['/Pages'].get('/Count', 0))
    except Exception:  # noqa: BLE001 - a broken catalogue is checked by the full count below
        declared = 0
    if declared > PDF_MAX_PAGES or len(reader.pages) > PDF_MAX_PAGES:
        raise ExtractionError(f'Upload a PDF with no more than {PDF_MAX_PAGES} pages.')
    pages = best_pdf_text(path, [p.extract_text() or '' for p in reader.pages])
    # Read each sparse page, including scanned pages inside a mixed PDF.
    needs_ocr = [
        len(text.strip()) < 60 or bool(page.images) for text, page in zip(pages, reader.pages, strict=False)
    ]
    if any(needs_ocr):
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(path)) as pdf:
            for i, text in enumerate(pages):
                if needs_ocr[i]:
                    pages[i] = _ocr_page(pdf, i, text)
    return '\n\n'.join(pages)


def read_document(path, clean=None):
    """Plain text of a DOCX, PDF or screenshot, cleaned and within size limits.

    `clean(text, screenshot)` tidies the text; agents can pass their own (the
    LinkedIn agent also removes browser and menu lines from screenshots).
    """
    path = Path(path)
    screenshot = path.suffix not in ('.docx', '.pdf')
    if path.suffix == '.docx':
        text = _docx_text(path)
    elif path.suffix == '.pdf':
        text = _pdf_text(path)
    else:
        # Screenshots: keep the page's main column, not its sidebar.
        with Image.open(path) as image:
            text = ocr(image, columns=True)
    text = (clean or tidy_text)(text, screenshot)
    if len(text) < MIN_TEXT_CHARS:
        raise ExtractionError('No readable text was found. Upload a clearer document or screenshot.')
    if len(text) > MAX_TEXT_CHARS:
        raise ExtractionError(
            'The extracted text exceeds 36,000 characters. Please upload a shorter document.'
        )
    return text
