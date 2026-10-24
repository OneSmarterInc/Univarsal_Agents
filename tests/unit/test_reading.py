"""Reading files, inside the separate limited process and directly."""

import pytest

from agents_service.documents.isolation import read_text
from agents_service.documents.reading import ExtractionError
from tests.conftest import make_settings, needs_tesseract


def test_pdf_is_read_in_a_separate_process(tmp_path, pdf_bytes):
    path = tmp_path / 'cv.pdf'
    path.write_bytes(pdf_bytes)
    text = read_text(path, make_settings(tmp_path, isolate_parsing=True))
    assert 'Data Analyst, Example Health' in text


@needs_tesseract
def test_screenshot_ocr_with_the_linkedin_cleaner(tmp_path, png_bytes):
    path = tmp_path / 'shot.png'
    path.write_bytes(png_bytes)
    text = read_text(
        path,
        make_settings(tmp_path, isolate_parsing=True),
        clean='agents_service.agents.linkedin.agent:screenshot_text',
    )
    assert 'Example Health' in text


def test_broken_file_gives_a_safe_message(tmp_path):
    path = tmp_path / 'cv.pdf'
    path.write_bytes(b'%PDF-1.4 broken secret-content')
    with pytest.raises(ExtractionError) as error:
        read_text(path, make_settings(tmp_path, isolate_parsing=True))
    assert 'secret' not in str(error.value)


def test_direct_reading_gives_the_same_text(tmp_path, pdf_bytes):
    path = tmp_path / 'cv.pdf'
    path.write_bytes(pdf_bytes)
    isolated = read_text(path, make_settings(tmp_path, isolate_parsing=True))
    direct = read_text(path, make_settings(tmp_path, isolate_parsing=False))
    assert isolated == direct


def _pdf(tmp_path, draw):
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    draw(page)
    page.save()
    path = tmp_path / 'cv.pdf'
    path.write_bytes(buffer.getvalue())
    return path


def test_text_drawn_out_of_order_is_read_in_reading_order(tmp_path):
    """Some CV templates draw every heading first and every date last."""
    from agents_service.documents.reading import read_document

    def draw(page):
        for text, y in (('EDUCATION', 700), ('PROJECTS', 600)):
            page.drawString(50, y, text)
        for text, y in (('Alex Example', 780), ('Bachelor of Engineering - Computer Science', 680),
                        ('Example Institute of Technology', 665), ('Weather Dashboard', 580),
                        ('Built a weather dashboard with HTML and JavaScript.', 565)):  # fmt: skip
            page.drawString(50, y, text)
        for text, y in (('2024 - 2028', 680), ('Mar 2025', 580)):
            page.drawRightString(550, y, text)

    lines = read_document(_pdf(tmp_path, draw)).splitlines()
    assert lines.index('EDUCATION') < lines.index('Example Institute of Technology') < lines.index('PROJECTS')
    assert 'Bachelor of Engineering - Computer Science 2024 - 2028' in [
        ' '.join(line.split()) for line in lines
    ]
    assert 'Weather Dashboard Mar 2025' in [' '.join(line.split()) for line in lines]


def test_a_two_column_cv_keeps_its_columns(tmp_path):
    """A full-width name line, then a sidebar and a main column, each stored as a block."""
    from agents_service.documents.reading import read_document

    left = ['SKILLS', 'Python', 'SQL', 'Kubernetes', 'EDUCATION', 'B.S. Computer Science', 'State University']
    right = ['EXPERIENCE', 'Data Analyst, Example Health Group, 2019 - 2022',
             'Built weekly reports for the clinical teams and managers.',
             'PROJECTS', 'Reduced manual counting time with a small Python tool.']  # fmt: skip

    def draw(page):
        page.drawString(40, 800, 'Sam Sample  |  sam@example.com  |  Springfield, USA  |  +1 202-555-0100')
        for i, text in enumerate(left):
            page.drawString(40, 760 - i * 18, text)
        for i, text in enumerate(right):
            page.drawString(220, 760 - i * 18, text)

    lines = [' '.join(line.split()) for line in read_document(_pdf(tmp_path, draw)).splitlines()]
    assert lines[1 : 1 + len(left)] == left
    assert lines[1 + len(left) :] == right


def test_a_page_in_reading_order_is_left_as_it_is(tmp_path):
    from agents_service.documents.reading import read_document

    rows = [
        'Alex Example',
        'EXPERIENCE',
        'Data Analyst, Example Health, 2021 - 2024',
        'Built reports.',
        'SKILLS',
        'SQL',
    ]

    def draw(page):
        for i, text in enumerate(rows):
            page.drawString(50, 780 - i * 18, text)

    assert read_document(_pdf(tmp_path, draw)).splitlines() == rows


def test_tight_lines_do_not_take_letters_from_their_neighbours(tmp_path):
    from agents_service.documents.reading import read_document

    def draw(page):  # 0.9 line spacing, drawn out of order so the page is re-read by position
        page.setFont('Helvetica', 12)
        for text, y in (('EXPERIENCE', 700), ('PROJECTS', 657)):
            page.drawString(50, y, text)
        for i, text in enumerate(['Software Engineer (2019 - 2022)', 'Did quality work', 'Built a gadget tool',
                                  'Planned yearly budgets']):  # fmt: skip
            page.drawString(50, 689.2 - i * 10.8 - (0 if i < 2 else 21.6), text)
        page.drawString(50, 760, 'Alex Example')

    text = read_document(_pdf(tmp_path, draw))
    for line in ('Software Engineer (2019 - 2022)', 'Did quality work', 'Built a gadget tool'):
        assert line in text.splitlines(), text


def test_an_overlapping_piece_starts_at_a_line_start():
    from agents_service.documents.text import chunks

    source = '\n'.join(f'Line {n} of a made-up CV with Example College of Engineering' for n in range(80))
    pieces = list(chunks(source, limit=1800, overlap=250))
    assert len(pieces) > 1
    assert all(piece.startswith('Line ') for piece in pieces)
    assert all(line in source.splitlines() for piece in pieces for line in piece.splitlines())
