"""OIG agent: screens a person against the HHS-OIG exclusion list (LEIE).

The list is a CSV that OIG publishes once a month. An operator loads it:

    agents tool oig load --file UPDATED.csv --as-of 2026-10-01

(--as-of is the date OIG published it, not today). Every worker then uses the
same copy. Until a list is loaded, jobs fail with not_configured.

Input: {"full_name": ..., "previous_names": [...], "date_of_birth": "YYYY-MM-DD",
"npi": ...}; a name or an NPI is required. The answer is one record,
section oig_exclusion, with details.outcome:

    no_match        this list, on that date, did not name them. Not a clearance.
    possible_match  a name matched and nothing else did: a person must check it.
    match           an NPI or date of birth also agreed.

details.matched_on says what tied a record to the person; details.searched_on
what the search used; details.source_as_of the list's publication date.

Settings (optional): AGENTS_OIG_MAX_AGE_DAYS (default 45): an older list still
screens, with a warning.
"""

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_service.agents.base import Agent, AgentResult, JobFailed, Tool
from agents_service.agents.oig.leie import corroborated_on, find, read_csv
from agents_service.agents.records import data_record
from agents_service.lookups.screening import screen_outcome
from agents_service.lookups.values import digits

SOURCE = 'https://oig.hhs.gov/exclusions/exclusions_list.asp'
LIST = 'leie'
# One parsed copy per worker process, replaced when a new list is loaded.
_LOADED = {'sha256': None, 'records': None, 'info': None}


class OigInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str | None = Field(default=None, max_length=200)
    previous_names: list[str] = Field(default_factory=list, max_length=10)
    date_of_birth: date | None = None
    npi: str | None = Field(default=None, max_length=20)

    @field_validator('npi')
    @classmethod
    def ten_digits(cls, value):
        if value is None:
            return None
        found = digits(value)
        if len(found) != 10:
            raise ValueError('an NPI has ten digits')
        return found

    @field_validator('previous_names')
    @classmethod
    def short_names(cls, value):
        if any(len(name) > 200 for name in value):
            raise ValueError('names are at most 200 characters')
        return value

    @model_validator(mode='after')
    def something_to_search(self):
        names = [n for n in [self.full_name or '', *self.previous_names] if n.strip()]
        if not names and not self.npi:
            raise ValueError('give a name or an NPI')
        for name in names:
            # A last name of two letters or more and something for the first name (a word or
            # an initial): the list is searched on both, so "Smith" alone or "J K" finds nothing.
            tokens = ''.join(ch if ch.isalnum() else ' ' for ch in name).split()
            if len(tokens) < 2 or not any(len(t) > 1 for t in tokens):
                raise ValueError('each name needs a first name (or initial) and a last name')
        return self


class OigSettings(BaseModel):
    max_age_days: int = Field(default=45, ge=1, le=3650)


class OigAgent(Agent):
    name = 'oig'
    title = 'OIG exclusion screen (US healthcare)'
    description = 'Searches the HHS-OIG list of people and firms excluded from US federal health programmes.'
    key_prefix = 'oig'
    input_kind = 'data'
    data_model = OigInput
    sections = ('oig_exclusion',)
    reader_version = 'oig/1'
    uses_model = False
    empty_is_answer = True
    settings_model = OigSettings

    def run(self, job):
        current = job.references.info(LIST)
        if current is None:
            raise JobFailed('not_configured')
        # The list and its date always come from the same file, even if a new one is loaded meanwhile.
        records, info = _records(job.references, current)
        job.check_cancelled()
        data = job.data
        aliases = [n for n in [*data.get('previous_names', []), data.get('full_name') or ''] if n.strip()]
        birth_date = date.fromisoformat(data['date_of_birth']) if data.get('date_of_birth') else None
        record, warnings = screen(records, aliases, birth_date, data.get('npi') or '', info['as_of'])
        age = (datetime.now(UTC).date() - date.fromisoformat(info['as_of'])).days
        if age > job.agent_settings.max_age_days:
            warnings.append(
                f'The exclusion list is {age} days old (published {info["as_of"]}). Load the latest one.'
            )
        return AgentResult(records=[record], warnings=warnings)

    def tools(self):
        return {
            'load': Tool(
                help='load the LEIE CSV that OIG published', add_arguments=_load_arguments, run=_load
            ),
            'status': Tool(help='show the loaded list', add_arguments=lambda parser: None, run=_status),
        }

    def readiness(self, settings, references):
        info = references.info(LIST)
        if info is None:
            return 'no exclusion list loaded (agents tool oig load)'
        age = (datetime.now(UTC).date() - date.fromisoformat(info['as_of'])).days
        limit = self.load_settings(settings).max_age_days
        return 'ok' if age <= limit else f'exclusion list is {age} days old (published {info["as_of"]})'


def _records(references, current):
    """(rows, info) of the list this worker holds, reloading it when a new one was loaded."""
    held = _LOADED['info']
    if held is None or (held['sha256'], held['loaded_at']) != (current['sha256'], current['loaded_at']):
        # Reloaded on a new file and also on the same file loaded again (a corrected --as-of).
        for attempt in (1, 2):
            try:
                with references.local_copy(LIST) as (path, info):
                    rows = read_csv(Path(path).read_bytes())
                break
            except Exception:  # noqa: BLE001 - local or S3: the file was replaced while being read
                if attempt == 2:
                    raise
                # A new list replaced the file while it was being read: read the current one.
        _LOADED.update(sha256=info['sha256'], records=rows, info=info)
    return _LOADED['records'], _LOADED['info']


def screen(records, aliases, birth_date, npi, as_of):
    """(record, warnings) for one person against the loaded list."""
    searched = []
    if aliases:
        searched.append('full_name')
    if len(aliases) > 1:
        searched.append('previous_names')
    if birth_date:
        searched.append('date_of_birth')
    if npi:
        searched.append('npi')
    hits = find(records, aliases, birth_date, npi)
    matched_on = corroborated_on(hits) if hits else searched
    outcome = screen_outcome(bool(hits), matched_on)
    details = {
        'outcome': outcome,
        'method': 'primary_source',
        'shape': 'screens',
        'source_as_of': as_of,
        'matched_on': matched_on,
        'searched_on': searched,
        'record_count': len(records),
        'hits': [hit.record.summary() for hit in hits[:20]],
        'hit_count': len(hits),
    }
    return data_record('oig_exclusion', details, SOURCE, as_of, value=outcome), _warnings(
        outcome, hits, as_of, searched
    )


def _warnings(outcome, hits, as_of, searched):
    if outcome == 'no_match':
        notes = [
            f'No matching record in the OIG exclusion list published {as_of}, searched on {", ".join(searched)}. '
            'This is not a clearance: it means this list, on that date, did not name them.'
        ]
        if 'date_of_birth' not in searched and 'npi' not in searched:
            notes.append(
                'Only a name was available to search on, so this result is weak in both directions. '
                'A date of birth or an NPI would make it worth relying on.'
            )
        return notes
    notes = []
    for hit in hits[:5]:
        record = hit.record
        since = record.excluded_on.isoformat() if record.excluded_on else 'an unrecorded date'
        where = f', {record.state}' if record.state else ''
        notes.append(f'{record.display_name()}{where}: excluded since {since}. {record.reason()}.')
        if record.waiver_state:
            notes.append(f'A waiver is recorded for {record.waiver_state}, which lets that state use them.')
    if len(hits) > 5:
        notes.append(f'{len(hits) - 5} further matching records are not listed here.')
    if outcome == 'possible_match':
        notes.append(
            'This is a name match and nothing more. OIG requires an identity check before a name is treated '
            'as the person, and the file has no SSN. A person must confirm or clear it; it is not a finding '
            'against them.'
        )
    else:
        notes.append(
            'Identity was corroborated on more than a name. A person must still confirm it before acting.'
        )
    return notes


def _load_arguments(parser: argparse.ArgumentParser):
    parser.add_argument('--file', required=True, help='UPDATED.csv from oig.hhs.gov')
    parser.add_argument('--as-of', required=True, help='the date OIG published it, YYYY-MM-DD')


def _load(context, args):
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        print('--as-of must be a date like 2026-10-01')
        return 2
    if as_of > datetime.now(UTC).date():
        print('--as-of is in the future; give the date OIG published the file')
        return 2
    path = Path(args.file)
    if not path.is_file():
        print(f'No such file: {args.file}')
        return 2
    raw = path.read_bytes()
    try:
        rows = read_csv(raw)
    except ValueError as exc:
        print(f'Not loaded: {exc}')
        return 2
    info = context.references.put(LIST, raw, '.csv', as_of.isoformat(), {'rows': len(rows)})
    print(f'Loaded {len(rows)} exclusions published {info["as_of"]} (fingerprint {info["sha256"][:12]}).')
    return 0


def _status(context, args):
    info = context.references.info(LIST)
    if info is None:
        print('No exclusion list loaded. Run: agents tool oig load --file UPDATED.csv --as-of YYYY-MM-DD')
        return 1
    print(
        f'{info["details"].get("rows", "?")} exclusions published {info["as_of"]} (fingerprint {info["sha256"][:12]}).'
    )
    return 0


AGENT = OigAgent()
