"""Shared by document agents: read each file, keep checked facts, build records.

A record is one fact found in the files:

    {"id", "section", "value", "details", "sources": [{"file_index", "sha256", "evidence"}]}

The same fact found in two files becomes one record with two sources.
"""

import hashlib
import json
import logging
import re
import traceback
from pathlib import Path

from pydantic import ValidationError

from agents_service.ai.providers import AIError
from agents_service.documents.matching import canonical
from agents_service.documents.reading import ExtractionError

log = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def headline_key(value):
    """Compare formatting only; never fuzzy-match or discard words or numbers."""
    value = canonical(value)
    # B.E., BE. and B.E, are common OCR variants. Keep decimal points and
    # meaningful symbols such as C++, C#, .NET, slashes and word hyphens.
    value = re.sub(r'(?<=[^\W\d_])[.,](?=\s|[^\W\d_]|$)', '', value)
    value = re.sub(r'\s*([|/@&])\s*', r'\1', value)
    return re.sub(r'\s+', ' ', value).strip()


def failure_message(exc, stage):
    """A safe message: never provider bodies, credentials or document text."""
    if isinstance(exc, AIError):
        return exc.public_message
    if isinstance(exc, ExtractionError):
        return str(exc)
    if isinstance(exc, ValidationError):
        return 'The model returned fields in an unsupported format.'
    if stage == 'read':
        return 'The file could not be read. Check the file and the OCR installation.'
    return 'The file could not be processed.'


def code_location(exc):
    """'agents_service/x.py:12' for the deepest frame inside this package."""
    where = None
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename).resolve()
        if PACKAGE_ROOT in path.parents and 'site-packages' not in path.parts:
            where = f'{path.relative_to(PACKAGE_ROOT).as_posix()}:{frame.lineno}'
    return where


def validate_response_size(value):
    """Reject, never silently truncate, oversized or deeply nested output."""
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 500_000:
        raise ExtractionError('The extracted result is too large.')
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 12 or (isinstance(item, str) and len(item) > 40_000):
            raise ExtractionError('An extracted field exceeds the supported size.')
        if isinstance(item, (list, dict)):
            if len(item) > 300:
                raise ExtractionError('Too many extracted entries.')
            pending.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))


def _entry_details(entry):
    return dict(entry) if isinstance(entry, dict) else {'value': entry, 'evidence': str(entry)}


def _display(details):
    return ' · '.join(
        ', '.join(map(str, v)) if isinstance(v, list) else str(v)
        for v in details.values()
        if v is not None and v != []
    )


def add_records(records, data, file, normalize_headline=False):
    """Add one file's facts to records, merging repeats. Returns how many facts it had."""
    count = 0
    for section, entries in data.items():
        if entries is None or entries == [] or entries == '':
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            details = _entry_details(entry)
            if not any(v for k, v in details.items() if k != 'evidence'):
                continue
            provenance = {
                'file_index': file.index,
                'sha256': file.sha256,
                'evidence': details.pop('evidence', ''),
            }
            identity_details = details
            if normalize_headline and section == 'headline':
                identity_details = {**details, 'value': headline_key(details.get('value', ''))}
            identity = json.dumps([section, identity_details], sort_keys=True, ensure_ascii=False)
            existing = next((r for r in records if r['_identity'] == identity), None)
            if existing:
                existing['sources'].append(provenance)
            else:
                records.append(
                    {
                        'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                        'section': section,
                        'value': _display(details),
                        'details': details,
                        'sources': [provenance],
                        '_identity': identity,
                    }
                )
            count += 1
    return count


REPORTED_REASONS = (
    'quality', 'context', 'timeout', 'length', 'format', 'capability', 'identity_ambiguous', 'connection', 'provider',
)  # fmt: skip


def diagnostic(exc, stage, unexpected):
    """Safe details of a failure for support: never document text or values."""
    info = {'stage': stage, 'exception_type': type(exc).__name__}
    if isinstance(exc, AIError):
        if exc.reason in REPORTED_REASONS:
            info['reason'] = exc.reason
        if exc.field and exc.field.startswith(('CV education', 'LinkedIn employment', 'conflicting')):
            info['field'] = exc.field.split(' ')[-1] if exc.field.startswith('conflicting') else exc.field
    if unexpected:
        info['where'] = code_location(exc)
    if isinstance(exc, ValidationError):
        info['validation_types'] = sorted(
            {e['type'] for e in exc.errors(include_input=False, include_url=False)}
        )
    return info


def failed_file(exc, index, stage):
    unexpected = not isinstance(exc, (AIError, ExtractionError, ValidationError))
    log.warning(
        'File failed: file_index=%s stage=%s exception_type=%s%s',
        index,
        stage,
        type(exc).__name__,
        f' where={code_location(exc)}' if unexpected else '',
    )
    return {
        'index': index,
        'status': 'failed',
        'error': failure_message(exc, stage),
        'diagnostic': diagnostic(exc, stage, unexpected),
    }


def read_files(files, read_one):
    """Run read_one(file, records, warnings) -> fact count, for every file.

    One file failing never stops the others. A cancelled job stops at once.
    Returns (records, file_entries, warnings).
    """
    records, entries, warnings = [], [], []
    for file in files:
        try:
            count = read_one(file, records, warnings)
            entries.append({'index': file.index, 'status': 'complete', 'records': count})
        except AIError as exc:
            if exc.reason == 'cancelled':
                raise
            entries.append(failed_file(exc, file.index, getattr(exc, 'stage', 'provider')))
        except Exception as exc:  # noqa: BLE001 - one file failing must not stop the others
            entries.append(failed_file(exc, file.index, getattr(exc, 'stage', 'read')))
    for record in records:
        record.pop('_identity', None)
    return records, entries, list(dict.fromkeys(warnings))


def stage(name):
    """Tag an exception with the stage it happened in (read, provider, validate)."""

    class _Stage:
        def __enter__(self):
            return self

        def __exit__(self, kind, exc, tb):
            if exc is not None and not hasattr(exc, 'stage'):
                try:
                    exc.stage = name
                except AttributeError:
                    pass
            return False

    return _Stage()


ALTERNATIVES = re.compile(r'^Multiple (.+) values were found\.')


def notice_for(text):
    """Tag a warning: 'action' needs a choice by the user, 'info' only explains."""
    match = ALTERNATIVES.match(text)
    if match:
        return {
            'level': 'action',
            'code': 'choose_alternative',
            'section': match.group(1).replace(' ', '_'),
            'text': text,
        }
    if text.startswith('Different headline wording'):
        return {'level': 'action', 'code': 'choose_alternative', 'section': 'headline', 'text': text}
    if text.startswith('Multiple possible CV owner names'):
        return {'level': 'action', 'code': 'owner_names', 'section': 'name', 'text': text}
    return {'level': 'info', 'code': 'excluded', 'section': None, 'text': text}


def job_state(records, files, empty_is_answer=False):
    """done, partial or failed.

    No records is a failure for a reader (nothing could be read), but a normal
    answer for a lookup agent that sets ``empty_is_answer`` (nothing was found).
    """
    if not records and not empty_is_answer:
        return 'failed'
    return 'partial' if any(f['status'] == 'failed' for f in files) else 'done'


# --- lookup (data) agents -------------------------------------------------------


def data_record(section, details, source, retrieved_at, evidence=''):
    """One fact from an outside source (a register, a list) for a data agent.

    `source` is where it came from (a public URL, never one carrying a key);
    `retrieved_at` is when the data was fetched or, for a published file, the
    date it was published.
    """
    details = {k: v for k, v in details.items() if v is not None}
    identity = json.dumps([section, details], sort_keys=True, ensure_ascii=False)
    return {
        'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
        'section': section,
        'value': _display(details),
        'details': details,
        'sources': [{'source': source, 'retrieved_at': retrieved_at, 'evidence': evidence}],
    }


FILE_SOURCE = {'file_index': int, 'sha256': str, 'evidence': str}
DATA_SOURCE = {'source': str, 'retrieved_at': str, 'evidence': str}


def check_records(records, sections):
    """Raise ValueError when an agent returns a record in the wrong shape.

    Every record needs a known section, a value, details and at least one
    source: a file source (file agents) or an outside source (data agents).
    """
    for record in records:
        if set(record) != {'id', 'section', 'value', 'details', 'sources'}:
            raise ValueError('record keys')
        if record['section'] not in sections or not isinstance(record['details'], dict):
            raise ValueError('record section or details')
        if not record['sources'] or not all(_source_ok(s) for s in record['sources']):
            raise ValueError('record sources')


def _source_ok(source):
    for shape in (FILE_SOURCE, DATA_SOURCE):
        if isinstance(source, dict) and set(source) == set(shape):
            return all(isinstance(source[k], kind) for k, kind in shape.items())
    return False
