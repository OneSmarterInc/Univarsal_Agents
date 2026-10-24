"""NPPES agent: looks up one US NPI (National Provider Identifier) at CMS.

Input: {"npi": "1234567893"}. A number that is not ten digits or fails the NPI
check digit is refused before anything is sent (422).

Every record says how it is known in details.method:

    primary_source  CMS issued and controls it: the NPI, its status, type,
                    issue date and the name it is issued to
    self_attested   the provider typed it into NPPES and CMS does not check it:
                    credential, taxonomy, practice state, former names and
                    licence numbers (claimed_registration)

An active NPI is not an active licence. Licence numbers come back as claims, to
be checked with the state board. This agent does not compare names: the
project decides whether the record is its person.

Settings (all optional): AGENTS_NPPES_API_URL, AGENTS_NPPES_TIMEOUT_SECONDS,
AGENTS_NPPES_CACHE_HOURS.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_service.agents.base import Agent, AgentResult, JobFailed, Note
from agents_service.agents.nppes.record import (
    check_digit_ok,
    first_record,
    former_names,
    is_organisation,
    licence_rows,
    parse_npi,
    practice_state,
    primary_taxonomy,
    provider_name,
    status_of,
)
from agents_service.agents.records import data_record
from agents_service.lookups.http import SourceError, get_json
from agents_service.lookups.values import clean, parse_date

PUBLIC_PAGE = 'https://npiregistry.cms.hhs.gov/provider-view/{npi}'


class NppesInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    npi: str = Field(max_length=20, description='Ten digits; spaces and dashes are allowed.')

    @field_validator('npi')
    @classmethod
    def valid_npi(cls, value):
        npi = parse_npi(value)
        if not npi or not check_digit_ok(npi):
            raise ValueError('not a valid NPI (ten digits with a matching check digit)')
        return npi


class NppesSettings(BaseModel):
    api_url: str = 'https://npiregistry.cms.hhs.gov/api/'
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    # Answers are reused for this long (0 = never). NPPES records change rarely.
    cache_hours: float = Field(default=24, ge=0, le=24 * 30)


class NppesAgent(Agent):
    name = 'nppes'
    title = 'NPPES (US NPI) lookup'
    description = 'Looks up a US National Provider Identifier at CMS and says how each fact is known.'
    key_prefix = 'nppes'
    input_kind = 'data'
    data_model = NppesInput
    sections = (
        'npi', 'full_name', 'npi_status', 'npi_type', 'npi_enumerated_on',
        'credential', 'taxonomy', 'practice_state', 'former_name', 'claimed_registration',
    )  # fmt: skip
    reader_version = 'nppes/1'
    uses_model = False
    settings_model = NppesSettings

    def run(self, job):
        npi = job.data['npi']
        config = job.agent_settings
        question = {'npi': npi}
        cached = job.cache.get(question) if config.cache_hours else None
        if cached is None:
            job.check_cancelled()
            cached = {
                'body': ask_cms(
                    npi, config, ipv4=getattr(getattr(job, 'settings', None), 'outbound_ipv4', True)
                ),
                'retrieved_at': datetime.now(UTC).isoformat(),
            }
            if config.cache_hours:
                job.cache.put(question, cached, config.cache_hours * 3600)
        records, warnings = read_record(npi, cached['body'], cached['retrieved_at'])
        return AgentResult(records=records, warnings=warnings)


def ask_cms(npi, config, transport=None, ipv4=True):
    try:
        body = get_json(
            config.api_url,
            params={'version': '2.1', 'number': npi},
            seconds=config.timeout_seconds,
            source='NPPES',
            transport=transport,
            ipv4=ipv4,
        )
    except SourceError as exc:
        # Down, slow or busy: worth one more try. Anything else: CMS refused the question.
        raise JobFailed('source_unavailable' if exc.kind in ('unavailable', 'rate_limited') else 'source_rejected',
                        retry=exc.kind in ('unavailable', 'rate_limited')) from None  # fmt: skip
    # NPPES answers a bad question with 200 and an "Errors" list: never read as "no such NPI".
    if isinstance(body, dict) and (body.get('Errors') or body.get('errors')):
        raise JobFailed('source_rejected')
    # Only a clear answer counts: a list of none or one record. Anything else (no list, two
    # records for one NPI) is unreadable, never "CMS has no record of this number".
    results = body.get('results') if isinstance(body, dict) else None
    if not isinstance(results, list) or len(results) > 1 or (results and not isinstance(results[0], dict)):
        raise JobFailed('source_unreadable')
    return body


def read_record(npi, body, retrieved_at):
    """(records, warnings) for one NPPES answer."""
    source = PUBLIC_PAGE.format(npi=npi)
    record = first_record(body)

    def add(section, details, method, value=None):
        records.append(data_record(section, {**details, 'method': method}, source, retrieved_at, value=value))

    records, warnings = [], []
    if record is None:
        # CMS was asked and has no such NPI: a finding, not a failure.
        add('npi', {'npi': npi, 'found': False}, 'primary_source', value=f'{npi} (not found)')
        warnings.append(
            Note('The NPI registry has no record of this number.', 'npi_not_found', section='npi')
        )
        return records, warnings

    basic = record.get('basic') or {}
    add(
        'npi',
        {'npi': npi, 'found': True, 'last_updated': clean(basic.get('last_updated'))},
        'primary_source',
        npi,
    )
    name = provider_name(record)
    if name:
        add('full_name', {'value': name}, 'primary_source', name)
    status = status_of(basic)
    add(
        'npi_status',
        {'value': status, 'deactivated_on': clean(basic.get('deactivation_date'))},
        'primary_source',
        status,
    )
    kind = 'organisation' if is_organisation(record) else 'individual'
    add('npi_type', {'value': kind}, 'primary_source', kind)
    issued = parse_date(basic.get('enumeration_date'))
    if issued:
        add('npi_enumerated_on', {'value': issued.isoformat()}, 'primary_source', issued.isoformat())

    # Everything below is the provider's own account of themselves.
    credential = clean(basic.get('credential'))
    if credential:
        add('credential', {'value': credential}, 'self_attested', credential)
    taxonomy = primary_taxonomy(record)
    if clean(taxonomy.get('desc')):
        add('taxonomy', {'value': clean(taxonomy.get('desc')), 'code': clean(taxonomy.get('code'))}, 'self_attested',
            clean(taxonomy.get('desc')))  # fmt: skip
    state = practice_state(record)
    if state:
        add('practice_state', {'value': state}, 'self_attested', state)
    for former in former_names(record):
        add('former_name', {'value': former}, 'self_attested', former)
    rows = licence_rows(record)
    for row in rows:
        label = f'{row["state"]} licence: {row["number"]}' if row['state'] else f'Licence: {row["number"]}'
        add(
            'claimed_registration',
            {
                'kind': 'licence',
                'number': row['number'],
                'jurisdiction': f'US-{row["state"]}' if row['state'] else 'US',
                'label': row['label'],
            },
            'self_attested',
            label,
        )

    if kind == 'organisation':
        warnings.append(
            Note(
                'This NPI belongs to an organisation, not to a person.',
                'organisation_npi',
                section='npi_type',
            )
        )
    if status != 'active':
        when = clean(basic.get('deactivation_date'))
        warnings.append(
            Note(
                f'CMS records this NPI as deactivated{f" on {when}" if when else ""}.',
                'npi_deactivated',
                section='npi_status',
            )
        )
    if rows:
        warnings.append(
            f'This record lists {len(rows)} licence number{"s" if len(rows) > 1 else ""}, typed in by the provider. '
            'CMS does not check them; none has been confirmed with a state board.'
        )
        warnings[-1] = Note(warnings[-1], 'unchecked_licences', section='claimed_registration')
    return records, warnings


AGENT = NppesAgent()
