"""Small, strict helpers for values read from outside sources."""

from datetime import date, datetime

# Values that mean "nothing here". Recording one as a fact would be worse than nothing.
EMPTY = {
    '',
    'none',
    'null',
    'n/a',
    'na',
    'unknown',
    'not found',
    'not stated',
    'not specified',
    'not provided',
    '-',
    '--',
}


def clean(value):
    """A trimmed string, or None when the value is empty or means "nothing"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value) if value else None
    text = str(value).strip()
    return None if text.lower() in EMPTY else text


def digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def parse_date(value):
    """A date from the usual written forms, or None. Never guesses a date from a part."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    for form in ('%Y-%m-%d', '%Y%m%d', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            return datetime.strptime(text[:10] if form == '%Y-%m-%d' else text, form).date()
        except ValueError:
            continue
    return None
