"""Read a file in a separate process.

A broken or hostile file can only harm that child process: it gets no secrets
(a scrubbed environment), a time limit, and on Linux also memory, CPU and file
limits. On Windows the limits are skipped; the process boundary and time limit
remain.
"""

import json
import os
import subprocess
import sys
from importlib import import_module

from agents_service.documents import reading
from agents_service.documents.reading import ExtractionError

SAFE_ENV = (
    'PATH',
    'SYSTEMROOT',
    'WINDIR',
    'TEMP',
    'TMP',
    'TMPDIR',
    'LANG',
    'LC_ALL',
    'TESSDATA_PREFIX',
    'HOME',
    'USERPROFILE',
)
TIMEOUT_SECONDS = 300


def resolve(clean):
    """'package.module:function' -> the function (None stays None)."""
    if not clean:
        return None
    module, _, name = clean.partition(':')
    return getattr(import_module(module), name)


def read_text(path, settings, clean=None):
    """The document's text. `clean` is an import path like 'pkg.mod:function'."""
    if not getattr(settings, 'isolate_parsing', True):
        reading.TESSERACT_CMD = settings.tesseract_cmd
        return reading.read_document(path, clean=resolve(clean))
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
    command = [
        sys.executable,
        '-m',
        'agents_service.documents.isolation',
        str(path),
        clean or '',
        settings.tesseract_cmd or '',
    ]
    try:
        proc = subprocess.run(command, env=env, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError('Reading this file took too long. Upload a smaller or clearer file.') from exc
    if len(proc.stdout) > 4 * 1024 * 1024:
        raise ExtractionError('The file produced more text than the processing limit.')
    try:
        answer = json.loads(proc.stdout or b'{}')
    except ValueError:
        answer = {}
    if 'text' in answer:
        return answer['text']
    raise ExtractionError(answer.get('error') or 'The file could not be read. It may be broken or protected.')


def _limit_resources():
    try:
        import resource
    except ImportError:  # Windows
        return
    resource.setrlimit(resource.RLIMIT_AS, (2048 * 1024**2, 2048 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    resource.setrlimit(resource.RLIMIT_FSIZE, (200 * 1024**2, 200 * 1024**2))


def main():
    _limit_resources()
    path, clean, tesseract = sys.argv[1], sys.argv[2], sys.argv[3]
    reading.TESSERACT_CMD = tesseract
    try:
        result = {'text': reading.read_document(path, clean=resolve(clean))}
    except ExtractionError as exc:
        result = {'error': str(exc)}
    except Exception:  # noqa: BLE001 - never send file content or tracebacks to the parent
        result = {'error': 'The file could not be read. It may be broken or protected.'}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
