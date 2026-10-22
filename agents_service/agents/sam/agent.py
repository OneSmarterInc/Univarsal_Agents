"""SAM agent: screens a person against SAM.gov's federal exclusion list
(debarments and exclusions from US federal contracts, across all agencies).

Needs a SAM.gov API key: AGENTS_SAM_API_KEY. A free personal key allows only
10 calls a day (AGENTS_SAM_DAILY_LIMIT, shared by every worker); production
needs a System Account key with a higher limit. Answers are cached for 24
hours (AGENTS_SAM_CACHE_HOURS), so the same name costs one call a day.

Input: {"full_name": ..., "previous_names": [...], "npi": ...}. One record,
section sam_exclusion, with details.outcome no_match / possible_match / match.
SAM publishes no date of birth, so a name hit is almost always only a
possible_match. A no_match covers federal contracting exclusions only and is
not a clearance.

Fails closed: an answer that cannot be read safely, more matches than can be
checked, a rejected key or a used-up daily limit end the job with an error
code. They never come back as no_match.
"""

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agents_service.agents.base import Agent, AgentResult, JobFailed
from agents_service.agents.records import data_record
from agents_service.agents.sam.response import (
    PAGE_SIZE,
    SamExclusion,
    Unreadable,
    dedupe,
    matches,
    name_matches,
    read_page,
)
from agents_service.lookups.http import SourceError, get_json
from agents_service.lookups.screening import name_words, screen_outcome
from agents_service.lookups.values import digits

SOURCE = 'https://sam.gov/search/?index=ex'


class SamInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str = Field(min_length=1, max_length=200)
    previous_names: list[str] = Field(default_factory=list, max_length=5)
    npi: str | None = Field(default=None, max_length=20)

    @field_validator('previous_names')
    @classmethod
    def short_names(cls, value):
        if any(len(name) > 200 for name in value):
            raise ValueError('names are at most 200 characters')
        return [name.strip() for name in value if name.strip()]

    @field_validator('full_name')
    @classmethod
    def a_real_name(cls, value):
        if len(name_words(value)) == 0:
            raise ValueError('give a name with at least one word of two letters')
        return value.strip()

    @field_validator('npi')
    @classmethod
    def ten_digits(cls, value):
        if value is None:
            return None
        if len(digits(value)) != 10:
            raise ValueError('an NPI has ten digits')
        return digits(value)


class SamSettings(BaseModel):
    api_key: SecretStr
    api_url: str = 'https://api.sam.gov/entity-information/v4/exclusions'
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    daily_limit: int = Field(default=10, ge=1)
    cache_hours: float = Field(default=24, ge=0, le=24 * 7)
    # Pages of 10 read per name; every page is one call against the daily limit.
    max_pages: int = Field(default=5, ge=1, le=100)
    # Calls one project may use per day (empty = no limit of its own beyond daily_limit),
    # so one busy project cannot use up SAM.gov for every other project.
    project_daily_limit: int | None = Field(default=None, ge=1)
    # Set to true once one live answer has been checked against the field mapping.
    mapping_verified: bool = False

    @field_validator('api_key')
    @classmethod
    def not_empty(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError('empty')
        return value


class SamAgent(Agent):
    name = 'sam'
    title = 'SAM.gov exclusion screen (US federal)'
    description = 'Searches SAM.gov for people and firms excluded from US federal contracts.'
    key_prefix = 'sam'
    input_kind = 'data'
    data_model = SamInput
    sections = ('sam_exclusion',)
    reader_version = 'sam/1'
    uses_model = False
    empty_is_answer = True
    settings_model = SamSettings

    def run(self, job):
        config = job.agent_settings
        data = job.data
        aliases = list(dict.fromkeys([*data.get('previous_names', []), data['full_name']]))
        aliases = [a for a in aliases if search_terms(a)]
        npi = data.get('npi') or ''
        candidates, fetched = [], []
        for alias in aliases:
            job.check_cancelled()
            found, when = search(job, alias, config)
            candidates.extend(found)
            fetched.append(when)
        record, warnings = screen(candidates, aliases, npi, min(fetched), config.mapping_verified)
        return AgentResult(records=[record], warnings=warnings)


# Not part of a listed name: a search for "Dr Jane Smith" must not need "Dr" in the record.
NOT_NAMES = {
    'dr', 'mr', 'mrs', 'ms', 'miss', 'mx', 'prof', 'rev', 'sir', 'jr', 'sr', 'ii', 'iii', 'iv',
    'md', 'do', 'rn', 'np', 'pa', 'phd', 'dds', 'dmd', 'lpn', 'aprn', 'crna', 'esq',
}  # fmt: skip


def search_terms(alias):
    """What is sent to SAM.gov for one name: its first and last name words only.

    SAM.gov puts AND between the words of exclusionName, so every word sent must be in
    the listed name. A middle name, a title or a suffix the listing does not carry would
    hide the person; the full name is still compared locally on every candidate.
    """
    words = [w for w in re.findall(r'[^\W\d_]+', alias.casefold()) if len(w) > 1 and w not in NOT_NAMES]
    if not words:
        return ''
    return words[0] if len(words) == 1 else f'{words[0]} {words[-1]}'


def search(job, alias, config, transport=None):
    """(exclusions, fetched_at) for one name: from the cache, or every page from SAM.gov."""
    terms = search_terms(alias)
    # The cache key is exactly what is sent, so two searches never share an answer by accident.
    question = {'name': terms}
    cached = job.cache.get(question) if config.cache_hours else None
    if cached is not None:
        return [SamExclusion.from_summary(s) for s in cached['records']], cached['fetched_at']
    found, page = [], 0
    while True:
        if config.project_daily_limit and not job.use_quota(
            f'project:{job.project}', config.project_daily_limit
        ):
            raise JobFailed('daily_limit_reached')
        if not job.use_quota('search', config.daily_limit):
            raise JobFailed('daily_limit_reached')
        try:
            records, total = read_page(_ask(terms, page, config, transport))
        except Unreadable:
            raise JobFailed('source_unreadable') from None
        found.extend(records)
        if not records:
            break
        if total is None:
            # No readable total: a full page means there may be more.
            if len(records) < PAGE_SIZE:
                break
        elif len(found) >= total:
            break
        page += 1
        if page >= config.max_pages:
            # More matches than can be read: reporting only part would hide a real hit.
            raise JobFailed('source_incomplete')
    fetched_at = datetime.now(UTC).isoformat()
    if config.cache_hours:
        job.cache.put(
            question,
            {'records': [r.summary() for r in found], 'fetched_at': fetched_at},
            config.cache_hours * 3600,
        )
    return found, fetched_at


def _ask(alias, page, config, transport):
    try:
        return get_json(
            config.api_url,
            params={
                'api_key': config.api_key.get_secret_value(),
                'exclusionName': alias,
                'page': page,
                'size': PAGE_SIZE,
            },
            seconds=config.timeout_seconds,
            source='SAM.gov',
            transport=transport,
        )
    except SourceError as exc:
        if exc.kind == 'unavailable':
            raise JobFailed('source_unavailable', retry=True) from None
        if exc.kind == 'unauthorized':
            raise JobFailed('not_configured') from None  # the key was refused
        if exc.kind == 'rate_limited':
            raise JobFailed('daily_limit_reached') from None
        if exc.kind == 'bad_response':
            raise JobFailed('source_unreadable') from None
        # SAM's gateway answers a refused key or a moved address with a bare 404.
        raise JobFailed('source_rejected') from None


def screen(candidates, aliases, npi, fetched_at, mapping_verified):
    """(record, warnings) from the candidates SAM.gov returned for these names."""
    wanted = [name_words(alias) for alias in aliases]
    hits = dedupe([c for c in candidates if c.is_active() and matches(c, wanted, npi)])
    searched = ['full_name'] + (['previous_names'] if len(aliases) > 1 else []) + (['npi'] if npi else [])
    if hits:
        # What tied each hit to the person, not what the search used.
        matched = set()
        for hit in hits:
            if npi and hit.npi == npi:
                matched.add('npi')
            if name_matches(hit, wanted):
                matched.add('full_name')
        matched_on = sorted(matched)
    else:
        matched_on = searched
    outcome = screen_outcome(bool(hits), matched_on)
    as_of = fetched_at[:10]
    details = {
        'outcome': outcome,
        'method': 'primary_source',
        'shape': 'screens',
        'source_as_of': as_of,
        'matched_on': matched_on,
        'searched_on': searched,
        'candidates_returned': len(candidates),
        'hits': [h.summary() for h in hits[:20]],
        'hit_count': len(hits),
        'mapping_verified': mapping_verified,
    }
    record = data_record('sam_exclusion', details, SOURCE, fetched_at, value=outcome)
    return record, _warnings(outcome, hits, as_of, searched, mapping_verified)


def _warnings(outcome, hits, as_of, searched, mapping_verified):
    notes = []
    if not mapping_verified:
        notes.append(
            'The SAM.gov field mapping has not yet been checked against a live answer on this service.'
        )
    if outcome == 'no_match':
        notes.append(
            f'No matching record in the SAM.gov exclusion list, checked {as_of} on {", ".join(searched)}. '
            'This is not a clearance: it covers federal contracting exclusions and nothing else.'
        )
        return notes
    for hit in hits[:5]:
        where = f', {hit.state}' if hit.state else ''
        agency = f' by {hit.agency}' if hit.agency else ''
        since = f' since {hit.active_from}' if hit.active_from else ''
        notes.append(
            f'{hit.name}{where}: excluded{agency}{since}. {hit.exclusion_type or "Exclusion type not stated"}.'
        )
    if len(hits) > 5:
        notes.append(f'{len(hits) - 5} further matching records are not listed here.')
    if outcome == 'possible_match':
        notes.append(
            'This is a name match and nothing more. SAM.gov publishes no date of birth, so a person must check it '
            'against the full entry. It is not a finding against them.'
        )
    else:
        notes.append(
            'Identity was corroborated on more than a name. A person must still confirm it before acting.'
        )
    return notes


AGENT = SamAgent()
