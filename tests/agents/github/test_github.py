"""GitHub agent, with made-up accounts and GitHub's answers faked (no network)."""

import json
import logging

import httpx
import pytest
import sqlalchemy
from fastapi.testclient import TestClient

from agents_service.agents.base import JobFailed
from agents_service.agents.github import agent as github
from agents_service.agents.github.account import activity, language_share, parse_username, shown_repos
from agents_service.agents.github.agent import (
    Client,
    GithubInput,
    GithubSettings,
    account_records,
    prove,
    read_account,
)
from agents_service.api.app import create_app
from agents_service.core.keys import create_key
from agents_service.services import build
from agents_service.worker import runner
from tests.conftest import auth, make_settings

SERVER = 'ghp_SERVER0000000000000000000000000000'
PERSONAL = 'gho_PERSON0000000000000000000000000000'
PROFILE = {
    'login': 'riya-example', 'id': 4242, 'type': 'User', 'html_url': 'https://github.com/riya-example',
    'name': 'Riya Example', 'bio': 'Student developer', 'company': None, 'location': 'Exampletown',
    'blog': '', 'public_repos': 4, 'followers': 7, 'created_at': '2021-05-04T10:00:00Z',
}  # fmt: skip


def repo(name, language='Python', fork=False, stars=0, pushed='2026-09-01T00:00:00Z'):
    return {
        'name': name, 'html_url': f'https://github.com/riya-example/{name}', 'language': language,
        'fork': fork, 'stargazers_count': stars, 'forks_count': 0, 'pushed_at': pushed,
        'created_at': '2025-01-01T00:00:00Z', 'description': f'{name} project', 'topics': ['demo'],
    }  # fmt: skip


REPOS = [
    repo('study-planner', 'Python', stars=3, pushed='2026-10-01T00:00:00Z'),
    repo('chat-app', 'JavaScript', stars=1, pushed='2026-08-01T00:00:00Z'),
    repo('ml-notes', 'Python', pushed='2026-07-01T00:00:00Z'),
    repo('linux', 'C', fork=True, stars=999, pushed='2026-10-20T00:00:00Z'),
]


class Job:
    """The parts of a running job the GitHub agent uses."""

    def __init__(self, data=None, quota=100):
        self.data, self.left, self.cache, self.project = (
            data or {'github': 'riya-example'},
            quota,
            Cache(),
            'test',
        )

    def use_quota(self, name, limit):
        self.left -= 1
        return self.left >= 0

    def check_cancelled(self):
        pass


class Cache(dict):
    def get(self, question):
        return super().get(json.dumps(question, sort_keys=True))

    def put(self, question, value, seconds):
        self[json.dumps(question, sort_keys=True)] = json.loads(json.dumps(value))


def github_answers(seen=None, gists=(), me=None, status=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if status:
            return httpx.Response(status, json={'message': 'no'})
        if path == '/users/riya-example':
            return httpx.Response(200, json=PROFILE)
        if path == '/users/riya-example/repos':
            return httpx.Response(200, json=REPOS)
        if path == '/users/riya-example/gists':
            return httpx.Response(
                200, json=[{k: v for k, v in g.items() if k != 'files_full'} for g in gists]
            )
        if path.startswith('/gists/'):
            gist = next(g for g in gists if g['id'] == path.split('/')[-1])
            return httpx.Response(200, json={**gist, 'files': gist.get('files_full', gist.get('files', {}))})
        if path == '/user':
            if request.headers.get('authorization') != f'Bearer {PERSONAL}':
                return httpx.Response(401, json={'message': 'Bad credentials'})
            return httpx.Response(200, json=me or {'login': 'riya-example', 'id': 4242})
        return httpx.Response(404, json={'message': 'Not Found'})

    return httpx.MockTransport(handler)


def client(job, handler, **settings):
    return Client(job, GithubSettings(token=SERVER, **settings), transport=handler)


# --- input ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('given', 'username'),
    [
        ('riya-example', 'riya-example'),
        ('@riya-example', 'riya-example'),
        ('github.com/riya-example', 'riya-example'),
        ('https://www.github.com/riya-example/', 'riya-example'),
        ('https://github.com/riya-example/study-planner?tab=readme', 'riya-example'),
    ],
)
def test_anything_a_person_pastes_gives_the_username(given, username):
    assert parse_username(given) == username


@pytest.mark.parametrize(
    'bad', ['', 'gitlab.com/riya', 'github.com/login', 'riya--x', '-riya', 'r' * 40, 'a b']
)
def test_what_is_not_a_username_is_refused(bad):
    with pytest.raises(ValueError):
        GithubInput(github=bad)


def test_proof_inputs_are_checked():
    assert GithubInput(github='riya-example', oauth_token=f' {PERSONAL} ').oauth_token == PERSONAL
    for bad in ({'oauth_token': 'short'}, {'proof_challenge': 'x y'}, {'other': 1}):
        with pytest.raises(ValueError):
            GithubInput(github='riya-example', **bad)


# --- what an account shows ------------------------------------------------------------


def test_forks_are_counted_but_never_credited_as_own_work():
    counts = activity(PROFILE, REPOS)
    assert (counts['original_repos'], counts['forked_repos'], counts['stars_on_original']) == (3, 1, 4)
    assert language_share([r for r in REPOS if not r['fork']]) == [('Python', 67), ('JavaScript', 33)]
    assert counts['last_push_on'] == '2026-10-20'
    assert [r['name'] for r in shown_repos(REPOS)] == ['study-planner', 'chat-app', 'ml-notes']


def test_records_say_how_each_fact_is_known():
    account = {'profile': PROFILE, 'repos': REPOS, 'fetched_at': '2026-10-23T05:00:00+00:00'}
    records, warnings = account_records('riya-example', account, GithubSettings())
    by = {}
    for record in records:
        by.setdefault(record['section'], []).append(record)
    assert by['github_account'][0]['details']['on_github_since'] == '2021'
    assert by['github_profile'][0]['details']['method'] == 'self_attested'  # typed by the holder
    assert by['github_activity'][0]['details']['method'] == 'primary_source'
    assert len(by['github_repository']) == 3
    assert by['github_repository'][0]['sources'][0]['source'].endswith('/study-planner')
    assert warnings == []


def test_an_organisation_and_a_profile_of_forks_are_said_so():
    org = {**PROFILE, 'type': 'Organization', 'public_repos': 300}
    account = {'profile': org, 'repos': [repo('x', fork=True)], 'fetched_at': 'now'}
    _, warnings = account_records('riya-example', account, GithubSettings())
    assert {w.code for w in warnings} == {'organisation_account', 'more_repositories', 'only_forks'}


# --- asking GitHub --------------------------------------------------------------------


def test_profile_and_repositories_are_read_with_the_server_token_and_cached():
    seen, job = [], Job()
    first = read_account(job, client(job, github_answers(seen)), GithubSettings(token=SERVER), 'riya-example')
    assert first['profile']['login'] == 'riya-example' and len(first['repos']) == 4
    assert all(r.headers['authorization'] == f'Bearer {SERVER}' for r in seen)
    again = read_account(job, client(job, github_answers(seen)), GithubSettings(token=SERVER), 'Riya-Example')
    assert again == first and len(seen) == 2  # the second read made no call


def test_no_such_account_is_an_answer_not_a_failure():
    job = Job({'github': 'nobody-here'})
    account = read_account(job, client(job, github_answers()), GithubSettings(token=SERVER), 'nobody-here')
    assert account['profile'] is None
    record = github.missing('nobody-here', account['fetched_at'])[0]
    assert record['details'] == {'username': 'nobody-here', 'found': False, 'method': 'primary_source'}


@pytest.mark.parametrize(
    ('status', 'code', 'retry'),
    [(401, 'not_configured', False), (403, 'source_unavailable', True), (429, 'source_unavailable', True),
     (503, 'source_unavailable', True), (422, 'source_rejected', False)],
)  # fmt: skip
def test_failures_have_their_own_codes(status, code, retry):
    job = Job()
    with pytest.raises(JobFailed) as caught:
        read_account(
            job, client(job, github_answers(status=status)), GithubSettings(token=SERVER), 'riya-example'
        )
    assert (caught.value.code, caught.value.retry) == (code, retry)


def test_an_answer_in_the_wrong_shape_is_never_read():
    def handler(request):
        return httpx.Response(
            200, json={'login': 'riya-example'} if 'repos' not in request.url.path else {'x': 1}
        )

    job = Job()
    with pytest.raises(JobFailed) as caught:
        read_account(job, client(job, httpx.MockTransport(handler)), GithubSettings(), 'riya-example')
    assert caught.value.code == 'source_unreadable'


def test_the_daily_limit_stops_before_calling():
    seen, job = [], Job(quota=0)
    with pytest.raises(JobFailed) as caught:
        read_account(job, client(job, github_answers(seen)), GithubSettings(), 'riya-example')
    assert caught.value.code == 'daily_limit_reached' and seen == []


# --- ownership ------------------------------------------------------------------------


def test_the_persons_own_token_proves_the_account_and_is_used_only_for_that():
    seen = []
    job = Job({'github': 'riya-example', 'oauth_token': PERSONAL})
    result = prove(client(job, github_answers(seen)), PROFILE, job.data)
    assert result['status'] == 'proved_oauth'
    assert [r.url.path for r in seen] == ['/user'] and seen[0].headers[
        'authorization'
    ] == f'Bearer {PERSONAL}'


def test_a_token_of_another_account_or_a_refused_token_proves_nothing():
    job = Job({'github': 'riya-example', 'oauth_token': PERSONAL})
    other = prove(client(job, github_answers(me={'login': 'someone-else', 'id': 1})), PROFILE, job.data)
    assert other['status'] == 'not_proved' and 'someone-else' in other['reason']
    job = Job({'github': 'riya-example', 'oauth_token': 'gho_' + 'X' * 36})
    assert prove(client(job, github_answers()), PROFILE, job.data)['status'] == 'not_proved'


def test_a_public_gist_with_the_code_proves_the_account():
    code = 'example-proof-7f3a9c'
    by_name = [{'id': 'g1', 'description': '', 'public': True, 'files': {f'{code}.txt': {}}}]
    in_text = [{'id': 'g2', 'description': 'notes', 'public': True, 'files': {'a.md': {}},
                'owner': {'login': 'riya-example'}, 'files_full': {'a.md': {'content': f'my code: {code}'}}}]  # fmt: skip
    for gists in (by_name, in_text):
        job = Job({'github': 'riya-example', 'proof_challenge': code})
        assert prove(client(job, github_answers(gists=gists)), PROFILE, job.data)['status'] == 'proved_gist'
    job = Job({'github': 'riya-example', 'proof_challenge': code})
    assert prove(client(job, github_answers(gists=[])), PROFILE, job.data)['status'] == 'not_proved'


def test_without_proof_the_account_is_not_proved_and_the_project_is_told():
    result = prove(client(Job(), github_answers()), PROFILE, {'github': 'riya-example'})
    notes = github.ownership_notes(result)
    assert (
        result['status'] == 'not_proved'
        and notes[0].code == 'ownership_not_proved'
        and notes[0].level == 'action'
    )


# --- the whole job ---------------------------------------------------------------------


@pytest.fixture
def github_service(tmp_path, monkeypatch):
    seen = []
    real = github.get_json
    monkeypatch.setattr(
        github, 'get_json', lambda *a, **kw: real(*a, **{**kw, 'transport': github_answers(seen)})
    )
    services = build(
        make_settings(tmp_path, enabled_agents='github', agent_settings={'github': {'token': SERVER}})
    )
    services.db.upgrade()
    token = create_key(services.db, services.settings.key_pepper, 'kormic', 'github', 'gh')[1]
    return services, TestClient(create_app(services)), token, seen


def test_a_job_from_start_to_end_and_no_token_is_kept(github_service, caplog):
    services, api, key, seen = github_service
    caplog.set_level(logging.DEBUG)
    assert api.get('/health/ready').json()['agents']['github'] == 'ok'
    job = api.post('/v1/github/jobs', headers=auth(key),
                   json={'data': {'github': 'https://github.com/riya-example', 'oauth_token': PERSONAL}}).json()  # fmt: skip
    runner.run(services, once=True)
    done = api.get(job['links']['self'], headers=auth(key)).json()
    assert done['state'] == 'done'
    sections = [r['section'] for r in done['result']['records']]
    assert sections.count('github_repository') == 3 and sections[-1] == 'github_ownership'
    assert done['result']['records'][-1]['value'] == 'proved_oauth'
    with services.db.session() as session:
        cache = [
            dict(row) for row in session.execute(sqlalchemy.text('SELECT * FROM lookup_cache')).mappings()
        ]
        jobs = [dict(row) for row in session.execute(sqlalchemy.text('SELECT * FROM jobs')).mappings()]
    kept = json.dumps(done) + caplog.text + json.dumps([cache, jobs], default=str)  # input deleted at the end
    assert cache  # the public account data was cached
    assert PERSONAL not in kept and SERVER not in kept


def test_a_bad_username_is_refused_before_any_call(github_service):
    services, api, key, seen = github_service
    response = api.post('/v1/github/jobs', headers=auth(key), json={'data': {'github': 'gitlab.com/riya'}})
    assert response.status_code == 422 and seen == []


def test_a_repository_without_a_name_is_never_shown():
    assert shown_repos([{'fork': False, 'stargazers_count': 0}, repo('ok')]) == shown_repos([repo('ok')])
