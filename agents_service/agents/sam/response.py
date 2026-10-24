"""Reading a SAM.gov exclusions answer. Ported from kormic-agents (sam.py).

Fails closed: an answer that is not recognisably a SAM exclusions answer
raises Unreadable instead of returning an empty list, because an empty list
means "searched, nothing found" and a body we could not read means no such
thing. The field paths come from GSA's published response sample.
"""

from dataclasses import dataclass, field

from agents_service.lookups.screening import name_words, same_person_or_firm
from agents_service.lookups.values import clean, digits, parse_date

# Response paths, tried in order (dotted). The first per field is GSA's current shape.
FIELDS = {
    'records': ('excludedEntity', 'excludedEntities', 'results'),
    'total': ('totalRecords', 'totalrecords', 'total'),
    'entity_name': ('exclusionIdentification.entityName', 'exclusionName', 'name'),
    'classification': ('exclusionDetails.classificationType', 'classificationType'),
    'exclusion_type': ('exclusionDetails.exclusionType', 'exclusionType'),
    'programme': ('exclusionDetails.exclusionProgram', 'exclusionProgram'),
    'agency': ('exclusionDetails.excludingAgencyName', 'excludingAgencyName'),
    'actions': ('exclusionActions.listOfActions', 'exclusionActions'),
    'active_from': ('activateDate', 'activationDate'),  # activateDate, not activationDate
    'active_to': ('terminationDate',),
    'termination_type': ('terminationType',),
    'status': ('recordStatus',),
    'npi': ('exclusionIdentification.npi', 'npi'),
    'state': ('exclusionPrimaryAddress.stateOrProvinceCode', 'exclusionPrimaryAddress.countryCode'),
}
NAME_PARTS = ('prefix', 'firstName', 'middleName', 'lastName', 'suffix')
# GSA allows 1 to 10 records per page.
PAGE_SIZE = 10


class Unreadable(Exception):
    """The answer was not a SAM exclusions answer we can read. Never "nothing found"."""


@dataclass
class SamExclusion:
    name: str = ''
    classification: str = ''
    exclusion_type: str = ''
    programme: str = ''
    agency: str = ''
    status: str = ''
    active_from: str = ''
    active_to: str = ''
    npi: str = ''
    state: str = ''
    words: set = field(default_factory=set)

    def is_active(self):
        """Absent or unreadable counts as active: an unreadable status is not evidence it lapsed."""
        return 'inactive' not in self.status.strip().lower()

    def summary(self):
        return {
            k: getattr(self, k) or None
            for k in ('name', 'classification', 'exclusion_type', 'programme', 'agency', 'state', 'active_from',
                      'active_to', 'npi', 'status')
        }  # fmt: skip

    @classmethod
    def from_summary(cls, summary):
        found = cls(**{k: v or '' for k, v in summary.items()})
        # A cached answer from before dates were given as YYYY-MM-DD.
        found.active_from = sam_date(found.active_from)
        found.active_to = sam_date(found.active_to, end=True)
        found.words = name_words(found.name)
        return found


def read_page(body):
    """(exclusions, total) of one answer page; total is None when SAM.gov did not give
    a readable one. Raises Unreadable."""
    if not isinstance(body, dict):
        raise Unreadable('not a JSON object')
    for key in ('error', 'errors', 'errorMessage', 'Errors'):
        if body.get(key):
            raise Unreadable('SAM.gov returned an error')
    total, records = pluck(body, FIELDS['total']), pluck(body, FIELDS['records'])
    if records is None and total is None:
        raise Unreadable('neither a record list nor a total: the answer shape was not recognised')
    count = as_int(total)
    if records is None:
        if count == 0:
            return [], 0
        raise Unreadable('a total without records')
    if not isinstance(records, list):
        raise Unreadable('the record list was not a list')
    parsed = [exclusion(entry) for entry in records if isinstance(entry, dict)]
    if parsed and not any(p.name for p in parsed):
        # Records came back and none had a readable name: the field mapping is wrong,
        # and every record would fail the name test and read as "no match".
        raise Unreadable('records without readable names: the field mapping needs checking')
    return parsed, count


def exclusion(entry):
    def get(key, source=entry):
        value = pluck(source, FIELDS[key])
        if isinstance(value, list):
            value = ', '.join(str(v) for v in value if v)
        return clean(value) or ''

    actions = pluck(entry, FIELDS['actions'])
    actions = [a for a in actions if isinstance(a, dict)] if isinstance(actions, list) else []
    # Any action still marked active makes the exclusion live (a history of actions is common).
    live = next((a for a in actions if _affirmatively_active(a.get('recordStatus'))), None)
    action = live or (actions[0] if actions else {})
    found = SamExclusion(
        name=record_name(entry),
        classification=get('classification'),
        exclusion_type=get('exclusion_type'),
        programme=get('programme'),
        agency=get('agency'),
        status=get('status', action),
        active_from=sam_date(get('active_from', action)),
        active_to=sam_date(get('active_to', action), end=True)
        or _indefinite(get('termination_type', action)),
        npi=digits(get('npi')),
        state=get('state'),
    )
    found.words = name_words(found.name)
    return found


def sam_date(value, end=False):
    """SAM.gov writes dates as MM-DD-YYYY; give them as YYYY-MM-DD. An end date in 2100 or
    later is SAM.gov's way of saying "no end date" and is given as "indefinite". A date
    that cannot be read is kept as written."""
    found = parse_date(value)
    if found is None:
        return value
    if end and found.year >= 2100:
        return 'indefinite'
    return found.isoformat()


def _indefinite(value):
    return 'indefinite' if value.strip().lower() == 'indefinite' else value


def record_name(entry):
    """A person's name from its parts, or a firm's entityName."""
    identification = entry.get('exclusionIdentification')
    if isinstance(identification, dict):
        assembled = ' '.join(p for p in (clean(identification.get(k)) for k in NAME_PARTS) if p)
        if assembled:
            return assembled
    return clean(pluck(entry, FIELDS['entity_name'])) or ''


def matches(record, aliases, npi):
    """SAM's own matching is looser than ours: what it returns are candidates only.

    Each name given is compared on its own (a former name never pools its words with
    the current one), and titles, suffixes and middle names do not hide a listing.
    """
    if npi and record.npi and record.npi == npi:
        return True
    return name_matches(record, aliases)


def name_matches(record, aliases):
    return bool(record.name) and any(same_person_or_firm(record.name, alias) for alias in aliases if alias)


def dedupe(records):
    """One entry per distinct record. Run on hits only, after the active check, so an
    inactive copy can never hide an active one; NPI and status are part of the key."""
    seen, out = set(), []
    for record in records:
        key = (
            record.name.lower(),
            record.agency.lower(),
            record.active_from,
            record.npi,
            record.status.lower(),
        )
        if key not in seen:
            seen.add(key)
            out.append(record)
    return out


def _affirmatively_active(value):
    text = str(value or '').strip().lower()
    return 'active' in text and 'inactive' not in text  # "active" is inside "inactive"


def pluck(body, paths):
    for path in paths:
        current = body
        for part in path.split('.'):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                current = None
                break
        if current not in (None, '', [], {}):
            return current
    return None


def as_int(value):
    try:
        return int(str(value).strip().replace(',', '').replace(' ', ''))
    except (TypeError, ValueError):
        return None
