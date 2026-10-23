"""GitHub agent: reads a public GitHub account and says how each fact is known.

Input: {"github": "<username or profile link>", "oauth_token": optional,
"proof_challenge": optional}. A value that is not a GitHub username is refused
at once (422) and never sent.

Records (details.method says how each fact is known):

    github_account     the account exists (or found: false), its type, id, url,
                       created on                                  primary_source
    github_profile     name, bio, company, location, blog: typed in by the
                       account holder; followers and repo counts   self_attested / primary_source
    github_activity    original vs forked repositories, languages by repository,
                       stars on original work, last push           primary_source
    github_repository  one per original repository, most recently pushed first
                       (at most 25)                                primary_source
    github_ownership   proved_oauth, proved_gist or not_proved, with the reason

GitHub is the source for the account; whether the account is the project's
person is a separate question, answered only by the ownership record:

    oauth_token        the person's own token (from the project's "Connect GitHub"
                       sign-in): GitHub says which account it belongs to. Used for
                       one call, never stored, cached or logged.
    proof_challenge    a code the project gave the person to put in a public gist
                       (its description, a file name or its text).

The agent never judges the person or the work: the project does.

Settings (all optional): AGENTS_GITHUB_TOKEN (a token with no scopes: 5,000
calls an hour instead of 60 per server), AGENTS_GITHUB_DAILY_LIMIT,
AGENTS_GITHUB_CACHE_HOURS, AGENTS_GITHUB_MAX_REPOS, AGENTS_GITHUB_TIMEOUT_SECONDS.
"""

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agents_service.agents.base import Agent, AgentResult, JobFailed, Note
from agents_service.agents.github.account import (
    activity,
    day,
    gist_holds,
    parse_username,
    shown_repos,
    year,
)
from agents_service.agents.records import data_record
from agents_service.lookups.http import SourceError, get_json
from agents_service.lookups.values import clean

PROFILE = 'https://github.com/{username}'
# Gists looked at for a proof: the newest ones; their text is read for at most this many.
GISTS_LISTED = 30
GISTS_READ = 5


class GithubInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    github: str = Field(max_length=300, description='A GitHub username or profile link.')
    # A plain string: input data is stored as JSON until the job ends, then deleted.
    oauth_token: str | None = Field(
        default=None,
        max_length=255,
        description="The person's own GitHub token, to prove the account is theirs.",
    )
    proof_challenge: str | None = Field(
        default=None, description='A code the person put in a public gist, to prove the account is theirs.'
    )

    @field_validator('github')
    @classmethod
    def a_username(cls, value):
        username = parse_username(value)
        if not username:
            raise ValueError('not a GitHub username or github.com profile link')
        return username

    @field_validator('oauth_token')
    @classmethod
    def token_shape(cls, value):
        if value is None or not value.strip():
            return None
        if not re.fullmatch(r'[A-Za-z0-9_\-]{20,255}', value.strip()):
            raise ValueError('not a GitHub token')  # the message never repeats the value
        return value.strip()

    @field_validator('proof_challenge')
    @classmethod
    def challenge_shape(cls, value):
        if value is None or not value.strip():
            return None
        if not re.fullmatch(r'[A-Za-z0-9_\-]{12,100}', value.strip()):
            raise ValueError('a proof code is 12-100 letters, digits, - or _')
        return value.strip()


class GithubSettings(BaseModel):
    token: SecretStr | None = None
    api_url: str = 'https://api.github.com'
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    # GitHub calls all projects may make per day (each job makes 2 to 4).
    daily_limit: int = Field(default=4000, ge=1)
    # Public account data is reused for this long (0 = never). Ownership is never cached.
    cache_hours: float = Field(default=24, ge=0, le=24 * 7)
    # Repositories read per account, most recently pushed first (100 per call).
    max_repos: int = Field(default=100, ge=1, le=300)

    @field_validator('token', mode='before')
    @classmethod
    def empty_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class GithubAgent(Agent):
    name = 'github'
    title = 'GitHub account'
    description = (
        'Reads a public GitHub account: profile, repositories, languages, activity and proof of ownership.'
    )
    key_prefix = 'gh'
    input_kind = 'data'
    data_model = GithubInput
    sections = (
        'github_account',
        'github_profile',
        'github_activity',
        'github_repository',
        'github_ownership',
    )
    reader_version = 'github/1'
    uses_model = False
    empty_is_answer = True
    settings_model = GithubSettings

    def run(self, job):
        config = job.agent_settings
        data = job.data
        username = data['github']
        ipv4 = getattr(getattr(job, 'settings', None), 'outbound_ipv4', True)
        client = Client(job, config, ipv4)
        account = read_account(job, client, config, username)
        if account['profile'] is None:
            return AgentResult(records=missing(username, account['fetched_at']), warnings=[
                Note(f'GitHub has no public account "{username}".', 'account_not_found', section='github_account')
            ])  # fmt: skip
        records, warnings = account_records(username, account, config)
        job.check_cancelled()
        ownership = prove(client, account['profile'], data)
        records.append(ownership_record(username, ownership))
        warnings.extend(ownership_notes(ownership))
        if not config.token:
            warnings.append(Note(
                'No server token is set (AGENTS_GITHUB_TOKEN): GitHub allows 60 calls an hour from this server.',
                'no_server_token', section='github_account',
            ))  # fmt: skip
        return AgentResult(records=records, warnings=warnings)


class Client:
    """GitHub calls for one job: counted against the shared daily limit, the server
    token (or none) by default, the person's token only where it is given."""

    def __init__(self, job, config, ipv4=True, transport=None):
        self.job, self.config, self.ipv4, self.transport = job, config, ipv4, transport

    def get(self, path, params=None, token=None, missing_ok=False):
        if not self.job.use_quota('calls', self.config.daily_limit):
            raise JobFailed('daily_limit_reached')
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        secret = token or (self.config.token.get_secret_value() if self.config.token else None)
        if secret:
            headers['Authorization'] = f'Bearer {secret}'
        try:
            return get_json(
                self.config.api_url.rstrip('/') + path,
                params=params,
                headers=headers,
                seconds=self.config.timeout_seconds,
                source='GitHub',
                transport=self.transport,
                ipv4=self.ipv4,
            )
        except SourceError as exc:
            if exc.status == 404 and missing_ok:
                return None
            if token and exc.kind == 'unauthorized':
                raise TokenRefused() from None
            if exc.kind == 'unauthorized' and exc.status == 401:
                raise JobFailed('not_configured') from None  # the server token was refused
            if exc.kind in ('unavailable', 'rate_limited') or exc.status == 403:
                # GitHub answers a used-up hourly limit with 403 or 429: try again later.
                raise JobFailed('source_unavailable', retry=True) from None
            if exc.kind == 'bad_response':
                raise JobFailed('source_unreadable') from None
            raise JobFailed('source_rejected') from None


class TokenRefused(Exception):
    """GitHub refused the person's own token (expired or revoked)."""


def read_account(job, client, config, username):
    """{'profile', 'repos', 'fetched_at'} from the cache or GitHub; profile None when there is no account."""
    question = {'username': username.lower(), 'max_repos': config.max_repos}
    cached = job.cache.get(question) if config.cache_hours else None
    if cached is not None:
        return cached
    job.check_cancelled()
    profile = client.get(f'/users/{username}', missing_ok=True)
    if profile is not None and (not isinstance(profile, dict) or not profile.get('login')):
        raise JobFailed('source_unreadable')
    repos = []
    if profile is not None:
        page = 1
        while len(repos) < config.max_repos:
            job.check_cancelled()
            batch = client.get(
                f'/users/{profile["login"]}/repos',
                params={'per_page': 100, 'page': page, 'sort': 'pushed', 'type': 'owner'},
            )
            if not isinstance(batch, list) or not all(isinstance(r, dict) for r in batch):
                raise JobFailed('source_unreadable')
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        repos = repos[: config.max_repos]
    account = {'profile': profile, 'repos': repos, 'fetched_at': datetime.now(UTC).isoformat()}
    if config.cache_hours:
        job.cache.put(question, account, config.cache_hours * 3600)
    return account


def missing(username, fetched_at):
    details = {'username': username, 'found': False, 'method': 'primary_source'}
    url = PROFILE.format(username=username)
    return [data_record('github_account', details, url, fetched_at, value=f'{username} (no public account)')]


def account_records(username, account, config):
    profile, repos, when = account['profile'], account['repos'], account['fetched_at']
    login = profile['login']
    url = clean(profile.get('html_url')) or PROFILE.format(username=login)
    records, warnings = [], []

    def add(section, details, value=None, source=url):
        records.append(data_record(section, details, source, when, value=value))

    kind = 'organisation' if profile.get('type') == 'Organization' else 'user'
    add('github_account', {
        'username': login, 'found': True, 'type': kind, 'id': profile.get('id'), 'url': url,
        'created_on': day(profile.get('created_at')), 'on_github_since': year(profile.get('created_at')),
        'method': 'primary_source',
    }, value=url)  # fmt: skip
    # What the account holder typed about themselves: GitHub does not check it.
    typed = {
        field: clean(profile.get(field))
        for field in ('name', 'bio', 'company', 'location', 'blog', 'twitter_username')
    }
    if any(typed.values()):
        add('github_profile', {**typed, 'method': 'self_attested'}, value=typed['name'] or login)
    counts = activity(profile, repos)
    add('github_activity', {**counts, 'followers': int(profile.get('followers') or 0), 'method': 'primary_source'},
        value=f'{counts["original_repos"]} of {counts["repos_read"]} repositories are original work')  # fmt: skip
    for repo in shown_repos(repos):
        add(
            'github_repository',
            {**repo, 'method': 'primary_source'},
            value=repo['name'],
            source=repo['url'] or url,
        )

    if kind == 'organisation':
        warnings.append(Note(f'"{login}" is an organisation account, not a person.', 'organisation_account',
                             section='github_account'))  # fmt: skip
    if counts['public_repos'] > counts['repos_read']:
        warnings.append(Note(
            f'The {counts["repos_read"]} most recently pushed of {counts["public_repos"]} public repositories '
            'were read (AGENTS_GITHUB_MAX_REPOS).', 'more_repositories', section='github_activity',
        ))  # fmt: skip
    if repos and not counts['original_repos']:
        warnings.append(Note('Every public repository read is a fork: none is shown as own work.',
                             'only_forks', section='github_activity'))  # fmt: skip
    if login.lower() != username.lower():
        warnings.append(
            Note(f'GitHub knows "{username}" as "{login}".', 'username_case', section='github_account')
        )
    return records, warnings


def prove(client, profile, data):
    """{'status': proved_oauth | proved_gist | not_proved, 'reason': ...} for this account."""
    login = profile['login']
    token = data.get('oauth_token')
    if token:
        try:
            me = client.get('/user', token=token)
        except TokenRefused:
            return {'status': 'not_proved', 'how': 'oauth', 'reason': 'GitHub refused the connected token.'}
        connected = str((me or {}).get('login') or '')
        if connected.lower() == login.lower() and (me or {}).get('id') == profile.get('id'):
            return {
                'status': 'proved_oauth',
                'how': 'oauth',
                'reason': 'The connected GitHub account is this one.',
            }
        return {
            'status': 'not_proved',
            'how': 'oauth',
            'reason': f'The connected GitHub account is "{connected or "unknown"}", not "{login}".',
        }
    challenge = data.get('proof_challenge')
    if challenge:
        gists = client.get(f'/users/{login}/gists', params={'per_page': GISTS_LISTED})
        if not isinstance(gists, list):
            raise JobFailed('source_unreadable')
        gists = [g for g in gists if isinstance(g, dict) and g.get('public', True)]
        if any(gist_holds(g, challenge) for g in gists):
            return {
                'status': 'proved_gist',
                'how': 'gist',
                'reason': 'A public gist of this account holds the code.',
            }
        for gist in gists[:GISTS_READ]:
            full = client.get(f'/gists/{gist.get("id")}', missing_ok=True) if gist.get('id') else None
            if (
                isinstance(full, dict)
                and str((full.get('owner') or {}).get('login', '')).lower() == login.lower()
            ):
                if gist_holds(full, challenge):
                    return {'status': 'proved_gist', 'how': 'gist',
                            'reason': 'A public gist of this account holds the code.'}  # fmt: skip
        return {
            'status': 'not_proved',
            'how': 'gist',
            'reason': 'No public gist of this account holds the code.',
        }
    return {
        'status': 'not_proved',
        'how': None,
        'reason': 'No proof was given (oauth_token or proof_challenge).',
    }


def ownership_record(username, ownership):
    details = {
        'value': ownership['status'],
        'how': ownership['how'],
        'reason': ownership['reason'],
        'method': 'primary_source' if ownership['status'] != 'not_proved' else 'self_attested',
    }
    when = datetime.now(UTC).isoformat()  # checked now, never from the cache
    return data_record('github_ownership', details, PROFILE.format(username=username), when,
                       value=ownership['status'])  # fmt: skip


def ownership_notes(ownership):
    if ownership['status'] != 'not_proved':
        return []
    return [Note(
        f'{ownership["reason"]} The account is the one named, but it is not proved to be the person\'s: '
        'ask them to connect GitHub or to publish the proof gist.',
        'ownership_not_proved', level='action', section='github_ownership',
    )]  # fmt: skip


AGENT = GithubAgent()
