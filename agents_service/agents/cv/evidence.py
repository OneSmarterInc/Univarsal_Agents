"""Keep only facts the document supports: every value must appear in its quotation, and every quotation in the document text."""

import re

from agents_service.agents.cv.owner import supported_owner
from agents_service.agents.cv.schemas import ResumeFacts
from agents_service.ai.providers import AIError
from agents_service.documents.matching import evidence_supported, value_supported

COLLECTIONS = (
    'education',
    'work_experience_entries',
    'projects',
    'publications',
    'certifications',
    'awards',
    'volunteering',
    'other_information',
)


REVIEW_SCALARS = (
    'email',
    'phone',
    'location',
    'summary',
    'institution',
    'major',
    'graduation_year',
    'gpa',
    'gpa_scale',
)


def supported_variants(variants, source):
    """Validate internal local-merge alternatives before emitting review records."""
    if not isinstance(variants, dict) or any(key not in REVIEW_SCALARS for key in variants):
        raise AIError('format')
    result = {}
    for field, entries in variants.items():
        if not isinstance(entries, list):
            raise AIError('format')
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'value', 'evidence'}:
                raise AIError('format')
            value = getattr(ResumeFacts.model_validate({field: entry['value']}), field)
            quote = entry['evidence']
            if (
                value is not None
                and isinstance(quote, str)
                and quote.strip()
                and evidence_supported(quote, source)
                and value_supported(str(value), quote)
            ):
                kept.append({'value': value, 'evidence': quote})
        if kept:
            result[field] = kept
    return result


def supported_owners(entries, source):

    if not isinstance(entries, list) or len(entries) > 32:
        raise AIError('format')
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'value', 'evidence'}:
            raise AIError('format')
        if supported_owner(entry['value'], entry['evidence'], source):
            kept.append(entry)
    return kept


def value_in_source(value, source):
    """Numbers match as numbers: a GPA of 3.8 is supported by "3.80", 4 by "4.0"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for match in re.finditer(r'(?<![\w.])\d+(?:\.\d+)?(?!\w|\.\d)', source):
            try:
                if abs(float(match.group()) - float(value)) < 1e-9:
                    return True
            except ValueError:
                continue
        return False
    return value_supported(str(value), source)


def supported_resume(data, source):
    """Retain source-supported fields only, without truncating record collections."""
    warnings = list(data.get('warnings') or [])
    for key, value in list(data.items()):
        if key == 'warnings':
            continue
        if key == '_owner_candidates':
            data[key] = supported_owners(value, source)
            continue
        if key == '_scalar_variants':
            data[key] = supported_variants(value, source)
            continue
        if key in COLLECTIONS:
            kept = []
            for original in value:
                item = dict(original)
                quote = item.get('evidence', '')
                if not quote or not evidence_supported(quote, source):
                    warnings.append(f'An unsupported {key.replace("_", " ")} record was excluded.')
                    continue
                for field, val in list(item.items()):
                    if field == 'evidence' or not val:
                        continue
                    if isinstance(val, list):
                        item[field] = [v for v in val if value_supported(v, quote)]
                    elif not value_supported(str(val), quote):
                        item[field] = None
                        warnings.append(f'An unsupported {field.replace("_", " ")} was excluded.')
                if item.get('title'):
                    kept.append(item)
            data[key] = kept
        elif isinstance(value, list):
            data[key] = [v for v in value if value_supported(str(v), source)]
        elif value is not None and not value_in_source(value, source):
            data[key] = None
            warnings.append(f'An unsupported {key.replace("_", " ")} was excluded.')
    # A GPA scale must be written as a scale ("8.9/10", "out of 10", "10-point scale").
    # A model asked for a scale the CV does not give tends to repeat the GPA instead.
    if data.get('gpa_scale') is not None and not stated_scale(data['gpa_scale'], source):
        data['gpa_scale'] = None
        warnings.append('An unsupported gpa scale was excluded.')
    variants = data.get('_scalar_variants') or {}
    if 'gpa_scale' in variants:
        variants['gpa_scale'] = [v for v in variants['gpa_scale'] if stated_scale(v['value'], v['evidence'])]
        if not variants['gpa_scale']:
            del variants['gpa_scale']
    data['warnings'] = list(dict.fromkeys(warnings))
    return data


def stated_scale(scale, text):
    """True when the text writes this number as a grading scale."""
    text_scale = str(scale).strip()
    try:
        value = float(text_scale)
    except ValueError:
        return False
    # 4, 4.0 and 4.00 are the same scale.
    number = rf'{int(value)}(?:\.0+)?' if value.is_integer() else re.escape(text_scale)
    patterns = (
        rf'/\s*{number}(?![\d.])',
        rf'\bout\s+of\s+{number}(?![\d.])',
        rf'\bscale\s*(?:of|:)?\s*{number}(?![\d.])',
        rf'(?<![\d.]){number}\s*-?\s*point\s+(?:scale|grading)',
    )
    return any(re.search(pattern, text, re.I) for pattern in patterns)
