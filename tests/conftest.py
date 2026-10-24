import io
import os
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

from agents_service.agents import registry
from agents_service.api.app import create_app
from agents_service.core.config import Settings
from agents_service.core.keys import create_key
from agents_service.services import build

# Run the whole suite on PostgreSQL too:
#   AGENTS_TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/agents_test pytest
TEST_DATABASE = os.environ.get('AGENTS_TEST_DATABASE_URL', '')


def find_tesseract():
    """Tesseract for the OCR tests: AGENTS_TESSERACT_CMD (environment or .env), else PATH."""
    from dotenv import dotenv_values

    given = os.environ.get('AGENTS_TESSERACT_CMD') or dotenv_values('.env').get('AGENTS_TESSERACT_CMD') or ''
    if given and os.path.exists(given):
        return given
    return shutil.which('tesseract') or ''


TESSERACT = find_tesseract()
needs_tesseract = pytest.mark.skipif(
    not TESSERACT, reason='Tesseract is not installed (set AGENTS_TESSERACT_CMD)'
)


def fresh_database(url):
    """An empty PostgreSQL schema for each test."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA public CASCADE'))
        connection.execute(text('CREATE SCHEMA public'))
    engine.dispose()
    return url


def make_settings(tmp_path, **overrides):
    values = {
        'database_url': fresh_database(TEST_DATABASE)
        if TEST_DATABASE
        else 'sqlite:///' + str(tmp_path / 'agents.db'),
        'data_dir': tmp_path / 'data',
        'key_pepper': 'test-pepper-' * 4,
        'isolate_parsing': False,
        'tesseract_cmd': TESSERACT,
        'poll_seconds': 0.01,
        '_env_file': None,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def services(settings):
    services = build(settings)
    services.db.upgrade()
    return services


@pytest.fixture
def client(services):
    return TestClient(create_app(services), raise_server_exceptions=False)


@pytest.fixture
def make_key(services):
    def make(agent='cv', project='project-a'):
        prefix = registry.load(agent).key_prefix
        return create_key(services.db, services.settings.key_pepper, project, agent, prefix)[1]

    return make


def auth(token):
    return {'Authorization': 'Bearer ' + token}


@pytest.fixture
def pdf_bytes():
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    lines = [
        'Alex Example',
        'alex@example.com | Columbus, Ohio',
        'Experience',
        'Data Analyst, Example Health, 2021 - 2024',
        'Education',
        'B.Sc. Computer Science, Example University, 2017 - 2021',
        'Skills: Python, SQL',
    ]
    for i, line in enumerate(lines):
        page.drawString(50, 780 - i * 20, line)
    page.save()
    return buffer.getvalue()


@pytest.fixture
def docx_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + ''.join(
                f'<w:p><w:r><w:t>{line}</w:t></w:r></w:p>'
                for line in ['Alex Example', 'Skills: Python, SQL, data analysis and reporting']
            )
            + '</w:body></w:document>',
        )
    return buffer.getvalue()


@pytest.fixture
def png_bytes():
    image = Image.new('RGB', (900, 300), 'white')
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 26)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(
        ['Alex Example', 'Data Analyst at Example Health', 'Columbus, Ohio, United States']
    ):
        draw.text((30, 30 + i * 60), line, fill='black', font=font)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()
