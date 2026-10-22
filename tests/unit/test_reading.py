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


def test_a_sidebar_is_read_as_its_own_column(tmp_path):
    from agents_service.documents.reading import read_document

    left = ['Sam Sample', 'sam@example.com', 'SKILLS', 'Python', 'SQL', 'Excel']
    right = ['EXPERIENCE', 'Data Analyst at Example Health Group, Springfield',
             'Built weekly reports for the clinical teams and managers.',
             'Automated the monthly data checks with Python scripts.', 'EDUCATION',
             'Bachelor of Science in Statistics, Example University']  # fmt: skip

    def draw(page):  # drawn line by line across both columns
        for i in range(max(len(left), len(right))):
            if i < len(left):
                page.drawString(40, 780 - i * 18, left[i])
            if i < len(right):
                page.drawString(220, 780 - i * 18, right[i])

    lines = read_document(_pdf(tmp_path, draw)).splitlines()
    assert lines[:6] == left
    assert lines[6:] == right
