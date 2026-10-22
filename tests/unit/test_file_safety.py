"""Hostile files: the API stays fast, the reader process does the slow checks,
and nothing of the file is left on disk after a read."""

import io
import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from agents_service.core.errors import ServiceError
from agents_service.documents import safety
from agents_service.documents.isolation import read_text
from agents_service.documents.reading import ExtractionError, read_document
from tests.conftest import auth, make_settings

EXTERNAL = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="r1" Type="http://x/oleObject" Target="http://evil.example/x" TargetMode="External"/>'
    '</Relationships>'
)


def docx_with(docx_bytes, name, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as source, zipfile.ZipFile(buffer, 'w') as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr(name, content)
    return buffer.getvalue()


def pdf_with_pages(n):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    for i in range(n):
        page.drawString(50, 780, f'Page {i} of a long document with enough words to read.')
        page.showPage()
    page.save()
    return buffer.getvalue()


def test_api_check_does_not_parse_word_links(docx_bytes):
    raw = docx_with(docx_bytes, 'word/_rels/document.xml.rels', EXTERNAL)
    started = time.monotonic()
    safety.check_docx(raw)  # accepted here: links are checked by the reader process
    assert time.monotonic() - started < 1


def test_huge_word_link_part_is_refused_without_parsing(docx_bytes):
    raw = docx_with(docx_bytes, 'word/_rels/big.rels', b'<a/>' + b' ' * (safety.MAX_RELS_BYTES + 1))
    with pytest.raises(ServiceError) as caught:
        safety.check_docx(raw)
    assert caught.value.code == 'decompression_limit'


def test_reader_refuses_word_file_with_outside_links(tmp_path, docx_bytes):
    path = tmp_path / 'cv.docx'
    path.write_bytes(docx_with(docx_bytes, 'word/_rels/document.xml.rels', EXTERNAL))
    with pytest.raises(ExtractionError, match='outside content'):
        read_document(path)


def test_api_check_does_not_walk_pdf_pages():
    raw = pdf_with_pages(40)
    started = time.monotonic()
    safety.check_pdf(raw)
    assert time.monotonic() - started < 0.1


def test_reader_refuses_long_pdf(tmp_path):
    path = tmp_path / 'cv.pdf'
    path.write_bytes(pdf_with_pages(31))
    with pytest.raises(ExtractionError, match='no more than 30 pages'):
        read_document(path)


def test_long_pdf_upload_is_accepted_then_fails_in_the_worker(client, make_key, services):
    from agents_service.worker import runner

    response = client.post(
        '/v1/cv/jobs',
        headers=auth(make_key('cv')),
        files={'files': ('cv.pdf', pdf_with_pages(31), 'application/pdf')},
    )
    assert response.status_code == 202
    runner.run(services, once=True)
    job = client.get(response.headers['location'], headers=auth(make_key('cv'))).json()
    assert job['state'] == 'failed'


def test_each_agent_has_its_own_upload_limit(client, make_key):
    from agents_service.agents import registry
    from agents_service.api.app import body_limit

    cv_limit = body_limit(registry.load('cv').file_rule(client.app.state.services.settings))
    big = b'%PDF-' + b'0' * (cv_limit + 10)
    response = client.post(
        '/v1/cv/jobs', headers={**auth(make_key('cv')), 'content-length': str(len(big) + 200)}, content=big
    )
    assert response.status_code == 413


SLOW_CLEANER = 'tests.unit.test_file_safety:slow_clean'


def slow_clean(text, screenshot):
    """Runs inside the reader process: starts a grandchild, writes temp files, then hangs."""
    import subprocess
    import sys
    import tempfile

    folder = tempfile.gettempdir()
    Path(folder, 'page.png').write_bytes(b'candidate page image')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    report = Path(sys.argv[1]).with_suffix('.report')
    report.write_text(json.dumps({'folder': folder, 'cwd': os.getcwd(), 'grandchild': child.pid}))
    time.sleep(120)


@pytest.mark.skipif(os.name != 'posix', reason='process groups are checked on Linux')
def test_timeout_kills_the_reader_and_its_children_and_leaves_no_files(tmp_path, docx_bytes):
    path = tmp_path / 'cv.docx'
    path.write_bytes(docx_bytes)
    settings = make_settings(tmp_path, isolate_parsing=True, file_seconds=5)
    with pytest.raises(ExtractionError, match='took too long'):
        read_text(path, settings, clean=SLOW_CLEANER)
    report = json.loads(path.with_suffix('.report').read_text())
    assert report['cwd'] == report['folder']  # the reader works in its own empty folder
    assert not Path(report['folder']).exists()  # deleted with everything in it
    time.sleep(0.2)
    assert not running(report['grandchild'])  # the grandchild was killed with the group


def running(pid):
    """True only for a live process (a killed one may linger as a zombie until reaped)."""
    status = Path(f'/proc/{pid}/status')
    if not status.exists():
        return False
    state = next(line for line in status.read_text().splitlines() if line.startswith('State:'))
    return state.split()[1] not in ('Z', 'X')


def test_reader_sees_no_secrets(tmp_path, docx_bytes, monkeypatch):
    monkeypatch.setenv('AGENTS_KEY_PEPPER', 'secret-pepper-' * 3)
    monkeypatch.setenv('AGENTS_MODEL_API_KEY', 'secret-key')
    path = tmp_path / 'cv.docx'
    path.write_bytes(docx_bytes)
    settings = make_settings(tmp_path, isolate_parsing=True, file_seconds=60)
    text = read_text(path, settings, clean='tests.unit.test_file_safety:show_environment')
    seen = json.loads(text)
    assert not any(k.startswith('AGENTS_') for k in seen['env'])
    assert seen['cwd'] != str(Path.cwd())


def show_environment(text, screenshot):
    return json.dumps({'env': sorted(os.environ), 'cwd': os.getcwd()})
