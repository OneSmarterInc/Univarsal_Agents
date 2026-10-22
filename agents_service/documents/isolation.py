"""Read a file in a separate process.

A broken or hostile file can only harm that child process: it gets no secrets
(a scrubbed environment), a time limit, and on Linux also memory, CPU and file
limits. On Windows the limits are skipped; the process boundary and time limit
remain.

Each read gets its own empty folder, which is the child's working folder and
temp folder. The child and everything it starts (tesseract) are one process
group, killed together when time runs out; the folder is then deleted, so no
page image or OCR text of the file stays on disk.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from importlib import import_module
from pathlib import Path

from agents_service.documents import reading
from agents_service.documents.reading import ExtractionError

SAFE_ENV = ('PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TESSDATA_PREFIX')
# Where agents_service lives, so the child imports it from any working folder.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])


def resolve(clean):
    """'package.module:function' -> the function (None stays None)."""
    if not clean:
        return None
    module, _, name = clean.partition(':')
    return getattr(import_module(module), name)


def _remove(folder):
    """Delete a reader folder. On Windows a killed reader can hold a file for a moment."""
    for attempt in range(5):
        shutil.rmtree(folder, ignore_errors=True)
        if not os.path.exists(folder):
            return
        time.sleep(0.2 * (attempt + 1))


def _remove_old_folders(hours=1):
    """Delete reader folders left by a crash (more than an hour old): they hold candidates' pages."""
    cutoff = time.time() - hours * 3600
    for old in Path(tempfile.gettempdir()).glob('agents-read-*'):
        try:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass


def read_text(path, settings, clean=None):
    """The document's text. `clean` is an import path like 'pkg.mod:function'."""
    if not getattr(settings, 'isolate_parsing', True):
        reading.TESSERACT_CMD = settings.tesseract_cmd
        return reading.read_document(path, clean=resolve(clean))
    _remove_old_folders()
    folder = tempfile.mkdtemp(prefix='agents-read-')
    try:
        stdout = _run_child(Path(path).resolve(), clean, settings, folder)
    finally:
        _remove(folder)
    if len(stdout) > 4 * 1024 * 1024:
        raise ExtractionError('The file produced more text than the processing limit.')
    try:
        answer = json.loads(stdout or b'{}')
    except ValueError:
        answer = {}
    if 'text' in answer:
        return answer['text']
    raise ExtractionError(answer.get('error') or 'The file could not be read. It may be broken or protected.')


def _run_child(path, clean, settings, folder):
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
    env.update(TMPDIR=folder, TEMP=folder, TMP=folder, HOME=folder, USERPROFILE=folder)
    env['PYTHONPATH'] = PACKAGE_ROOT
    command = [
        sys.executable,
        '-m',
        'agents_service.documents.isolation',
        str(path),
        clean or '',
        absolute_command(settings.tesseract_cmd),
        str(settings.file_seconds),
    ]
    # Own process group: Ctrl+C on the worker ("stop after this job") must not kill the
    # reader, and on timeout the whole group (with tesseract) is killed at once.
    group = (
        {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == 'win32'
        else {'start_new_session': True}
    )
    proc = subprocess.Popen(
        command,
        env=env,
        cwd=folder,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        **group,
    )
    try:
        stdout, _ = proc.communicate(timeout=settings.file_seconds)
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError('Reading this file took too long. Upload a smaller or clearer file.') from exc
    finally:
        _kill_group(proc)
    return stdout


def absolute_command(command):
    """A relative program path made absolute, since the reader runs in its own folder.

    A bare name such as "tesseract" is looked up on PATH and stays as it is.
    """
    if not command or (os.sep not in command and (os.altsep or '/') not in command):
        return command or ''
    return str(Path(command).resolve())


def _kill_group(proc):
    """Stop the child and anything it started; safe to call when all have ended."""
    if sys.platform == 'win32':
        if proc.poll() is None:
            taskkill = Path(os.environ.get('SYSTEMROOT', r'C:\Windows'), 'System32', 'taskkill.exe')
            subprocess.run(
                [str(taskkill), '/F', '/T', '/PID', str(proc.pid)], capture_output=True, check=False
            )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    if proc.stdout:
        proc.stdout.close()


def _limit_resources(seconds):
    try:
        import resource
    except ImportError:  # Windows
        return
    resource.setrlimit(resource.RLIMIT_AS, (2048 * 1024**2, 2048 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (200 * 1024**2, 200 * 1024**2))


def main():
    path, clean, tesseract, seconds = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    _limit_resources(seconds)
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
