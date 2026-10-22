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
