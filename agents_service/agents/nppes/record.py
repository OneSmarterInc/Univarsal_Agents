"""Reading one NPPES record. Pure functions, ported from kormic-agents (nppes.py).

What CMS establishes: the NPI exists, its status, person or organisation, the
date it was issued and the name it is issued to. What the provider only typed
in and CMS never checks: the credential, taxonomy, practice address, former
names and, above all, state licence numbers.
"""

import re

from agents_service.lookups.values import clean

# CMS puts this in front of an NPI before computing its check digit.
NPI_PREFIX = '80840'


def parse_npi(value):
    """Ten digits, however they were pasted; '' when it is not ten digits."""
    found = re.sub(r'\D', '', str(value or ''))
    return found if len(found) == 10 else ''


def check_digit_ok(npi):
    """The NPI's own checksum (Luhn over 80840 + the first nine digits)."""
    if len(npi) != 10 or not npi.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(NPI_PREFIX + npi[:9])):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10 == int(npi[9])


def first_record(body):
    """The one record of an exact-NPI search, or None. Never chooses between two."""
    results = body.get('results') if isinstance(body, dict) else None
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        return None
    return results[0]


def is_organisation(record):
    return str(record.get('enumeration_type') or '').upper() == 'NPI-2'


def status_of(basic):
    """'active' only for CMS's "A"; anything else, even empty, is not active."""
    return 'active' if str(basic.get('status') or '').strip().upper() == 'A' else 'deactivated'


def provider_name(record):
    basic = record.get('basic') or {}
    organisation = clean(basic.get('organization_name'))
    if organisation:
        return organisation
    return ' '.join(p for p in (clean(basic.get(k)) for k in ('first_name', 'middle_name', 'last_name')) if p)


def former_names(record):
    found = []
    for entry in record.get('other_names') or []:
        if not isinstance(entry, dict):
            continue
        organisation = clean(entry.get('organization_name'))
        name = organisation or ' '.join(
            p for p in (clean(entry.get(k)) for k in ('first_name', 'middle_name', 'last_name')) if p
        )
        if name and name not in found:
            found.append(name)
    return found


def licence_rows(record):
    """Every licence the record claims, once per (state, number)."""
    rows, seen = [], set()
    for entry in record.get('taxonomies') or []:
        if not isinstance(entry, dict):
            continue
        number = clean(entry.get('license'))
        if not number:
            continue
        state = (clean(entry.get('state')) or '').upper()
        key = (state, number.upper())
        if key in seen:
            continue
        seen.add(key)
        rows.append({'number': number, 'state': state, 'label': clean(entry.get('desc')) or ''})
    return rows


def primary_taxonomy(record):
    taxonomies = [t for t in (record.get('taxonomies') or []) if isinstance(t, dict)]
    if not taxonomies:
        return {}
    return next((t for t in taxonomies if t.get('primary')), taxonomies[0])


def practice_state(record):
    """The practice location's state, not the mailing address's."""
    for address in record.get('addresses') or []:
        if isinstance(address, dict) and str(address.get('address_purpose') or '').upper() == 'LOCATION':
            return clean(address.get('state')) or ''
    return ''
