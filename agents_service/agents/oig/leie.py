"""The HHS-OIG List of Excluded Individuals and Entities (LEIE): reading the
published CSV and searching it. Pure functions, ported from kormic-agents (oig.py).

OIG publishes the LEIE as one CSV a month at
https://oig.hhs.gov/exclusions/exclusions_list.asp. A name match is never an
identification: the file has no SSN, and names collide and change.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from agents_service.lookups.screening import name_words
from agents_service.lookups.values import digits

REQUIRED_COLUMNS = ('LASTNAME', 'FIRSTNAME', 'BUSNAME', 'NPI', 'DOB', 'EXCLTYPE', 'EXCLDATE', 'REINDATE')

# The legal basis of an exclusion, in words a reviewer can read.
EXCLUSION_REASONS = {
    '1128a1': 'Convicted of a Medicare or Medicaid related crime',
    '1128a2': 'Convicted of patient abuse or neglect',
    '1128a3': 'Felony conviction for health care fraud',
    '1128a4': 'Felony conviction relating to controlled substances',
    '1128b1': 'Misdemeanour conviction for health care fraud',
    '1128b2': 'Convicted of obstructing an investigation',
    '1128b3': 'Misdemeanour conviction relating to controlled substances',
    '1128b4': 'Their licence was revoked, suspended, or surrendered',
    '1128b5': 'Excluded or suspended from another federal or state health programme',
    '1128b6': 'Claimed excessive charges or unnecessary services',
    '1128b7': 'Fraud, kickbacks, or other prohibited activity',
    '1128b8': 'An entity controlled by a sanctioned individual',
    '1128b14': 'Defaulted on a health education loan',
    '1128b15': 'Controlled a sanctioned entity',
}


@dataclass(slots=True)
class Exclusion:
    last_name: str = ''
    first_name: str = ''
    middle_name: str = ''
    business_name: str = ''
    general: str = ''
    specialty: str = ''
    npi: str = ''
    date_of_birth: date | None = None
    state: str = ''
    exclusion_type: str = ''
    excluded_on: date | None = None
    reinstated_on: date | None = None
    waiver_state: str = ''
    # Computed once: the list has ~84,000 rows and every screen walks all of them.
    last_words: set = field(default_factory=set)
    first_words: set = field(default_factory=set)
    business_words: set = field(default_factory=set)
    first_initial: str = ''

    def __post_init__(self):
        self.last_words = name_words(self.last_name)
        self.first_words = name_words(self.first_name)
        self.business_words = name_words(self.business_name)
        letters = [ch for ch in self.first_name.casefold() if ch.isalpha()]
        self.first_initial = letters[0] if letters else ''

    def display_name(self):
        return self.business_name or ' '.join(
            p for p in (self.first_name, self.middle_name, self.last_name) if p
        )

    def reason(self):
        key = self.exclusion_type.replace('.', '').replace(' ', '').lower()
        return EXCLUSION_REASONS.get(key, f'Exclusion type {self.exclusion_type}')

    def summary(self):
        return {
            'name': self.display_name(),
            'npi': self.npi or None,
            'state': self.state or None,
            'specialty': self.specialty or None,
            'exclusion_type': self.exclusion_type or None,
            'reason': self.reason(),
            'excluded_on': self.excluded_on.isoformat() if self.excluded_on else None,
            'waiver_state': self.waiver_state or None,
            'date_of_birth_known': self.date_of_birth is not None,
        }


def leie_date(value):
    """LEIE dates are YYYYMMDD, with 00000000 for unknown; unknown stays None."""
    found = digits(value)
    if len(found) != 8 or found == '00000000':
        return None
    try:
        return datetime.strptime(found, '%Y%m%d').date()
    except ValueError:
        return None


def row_to_exclusion(row):
    def get(name):
        value = row.get(name) or row.get(name.lower()) or ''
        return value.strip()

    npi = digits(get('NPI'))
    return Exclusion(
        last_name=get('LASTNAME'),
        first_name=get('FIRSTNAME'),
        middle_name=get('MIDNAME'),
        business_name=get('BUSNAME'),
        general=get('GENERAL'),
        specialty=get('SPECIALTY'),
        # OIG writes 0 (or ten zeroes) for "no NPI"; only ten digits are an NPI.
        npi=npi if len(npi) == 10 and npi != '0000000000' else '',
        date_of_birth=leie_date(get('DOB')),
        state=get('STATE'),
        exclusion_type=get('EXCLTYPE'),
        excluded_on=leie_date(get('EXCLDATE')),
        reinstated_on=leie_date(get('REINDATE')),
        waiver_state=get('WVRSTATE'),
    )


def read_csv(raw):
    """All rows of an LEIE CSV. Raises ValueError when it is not the LEIE."""
    text = raw.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    columns = {c.strip().upper() for c in reader.fieldnames or []}
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise ValueError(f'Not the LEIE file: missing columns {", ".join(missing)}')
    rows = [row_to_exclusion({(k or '').strip().upper(): v or '' for k, v in row.items()}) for row in reader]
    if not rows:
        raise ValueError('The LEIE file has no rows.')
    return rows


@dataclass
class Hit:
    record: Exclusion
    on_name: bool
    on_npi: bool
    on_birth_date: bool


def find(records, aliases, birth_date, npi):
    """Records that could be this person. Over-inclusive on purpose: a missed exclusion
    is the one failure a screen exists to prevent; extra hits go to a person to check."""
    wanted = Wanted(aliases)
    found = []
    for record in records:
        if record.reinstated_on:
            continue  # back in the programmes
        on_npi = bool(npi and record.npi and record.npi == npi)
        on_name = _name_matches(record, wanted)
        if not on_npi and not on_name:
            continue
        if not on_npi and birth_date and record.date_of_birth and record.date_of_birth != birth_date:
            continue  # a different date of birth: a different person with the same name
        on_birth_date = bool(birth_date and record.date_of_birth and record.date_of_birth == birth_date)
        found.append(Hit(record, on_name, on_npi, on_birth_date))
    return found


# Words that say what kind of firm it is, not whose.
FIRM_WORDS = {
    'inc', 'llc', 'ltd', 'co', 'corp', 'corporation', 'company', 'pc', 'pa', 'pllc', 'llp', 'lp', 'md', 'dds',
    'dmd', 'do', 'the', 'of', 'and', 'services', 'service', 'group', 'center', 'clinic', 'medical', 'health',
}  # fmt: skip


class Wanted:
    """The names searched for, as words, initials given on their own, and first letters."""

    def __init__(self, aliases):
        tokens = [t for alias in aliases for t in _tokens(alias)]
        self.words = {t for t in tokens if len(t) > 1}
        # Each name on its own, for firm names (see _name_matches).
        self.names = [w for w in ({t for t in _tokens(alias) if len(t) > 1} for alias in aliases) if w]
        self.initials_given = {t for t in tokens if len(t) == 1}
        self.first_letters = {t[0] for t in tokens}


def _tokens(name):
    return ''.join(ch if ch.isalnum() else ' ' for ch in str(name or '').casefold()).split()


def _name_matches(record, wanted):
    """Over-inclusive: the last name must match; the first name matches by word or initial.

    The list writes some first names as an initial ("J"), and some people have no first
    name at all; both are still found. An initial given in the search ("J Doe") matches a
    listed first name starting with it.
    """
    if record.business_name:
        # A firm matches when it carries the whole name ("BRIAN JENKINS DDS PA" for Brian
        # Jenkins), or the name carries the whole firm name. One shared word is not enough:
        # it made every "Brian" a possible match for "BRIAN H JENKINS, D D S, P A".
        core = record.business_words - FIRM_WORDS
        return any(name <= record.business_words or (core and core <= name) for name in wanted.names)
    if not record.last_words or not record.last_words & wanted.words:
        return False
    if record.first_words:
        return bool(record.first_words & wanted.words) or record.first_initial in wanted.initials_given
    if record.first_initial:  # the list gives only an initial
        return record.first_initial in wanted.first_letters
    return True  # no first name on the list


def corroborated_on(hits):
    """What tied the hits to this person: only fields present on both sides count."""
    found = set()
    for hit in hits:
        if hit.on_name:
            found.add('full_name')
        if hit.on_npi:
            found.add('npi')
        if hit.on_birth_date:
            found.add('date_of_birth')
    return sorted(found)
