"""SAM agent: ported from kormic-agents' tests, plus paging, the daily limit, the cache and key safety."""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from agents_service.agents.base import JobFailed
from agents_service.agents.sam.agent import SamInput, SamSettings, screen, search
from agents_service.agents.sam.response import Unreadable, read_page
from agents_service.api.app import create_app
from agents_service.core.keys import create_key
from agents_service.services import build
from agents_service.worker import runner
from tests.conftest import auth, make_settings

KEY = 'SECRET-SAM-KEY-123'
NOW = '2026-10-22T10:00:00+00:00'
PERSON = {
    'exclusionDetails': {
        'classificationType': 'Individual', 'exclusionType': 'Prohibition/Restriction',
        'exclusionProgram': 'Reciprocal', 'excludingAgencyName': 'HEALTH AND HUMAN SERVICES, DEPARTMENT OF',
    },
    'exclusionIdentification': {'npi': None, 'firstName': 'AMARA', 'middleName': None, 'lastName': 'OKAFOR',
                                'entityName': 'AMARA OKAFOR'},
    'exclusionActions': {'listOfActions': [{'activateDate': '03-09-2021', 'terminationType': 'Indefinite',
                                            'recordStatus': 'Active'}]},
    'exclusionPrimaryAddress': {'stateOrProvinceCode': 'CA', 'countryCode': 'USA'},
}  # fmt: skip
FIRM = {
    'exclusionDetails': {'classificationType': 'Firm', 'excludingAgencyName': 'DEFENSE LOGISTICS AGENCY'},
    'exclusionIdentification': {'entityName': 'Supreme Fuels GmbH & Co. KG'},
    'exclusionActions': {'listOfActions': [{'activateDate': '03-13-2015', 'recordStatus': 'Active'}]},
}


def page(*records, total=None):
    return {'totalRecords': len(records) if total is None else total, 'excludedEntity': list(records)}


def run(records, full_name='Amara Okafor', previous=(), npi=''):
    found = []
    for body in records if isinstance(records, list) else [records]:
        found.extend(read_page(body)[0])
    aliases = [*previous, full_name]
    record, warnings = screen(found, aliases, npi, NOW, False)
    return record['details'], ' '.join(warnings)


# --- failing closed ------------------------------------------------------------------


@pytest.mark.parametrize('body', [{'somethingElse': []}, {'data': {'items': []}}, [], 'text', {},
                                  {'errorMessage': 'invalid api key'}, {'totalRecords': 12},
                                  {'totalRecords': 2, 'excludedEntity': [{'other': 1}, {'shape': 2}]}])  # fmt: skip
def test_an_answer_that_cannot_be_read_is_never_a_clean_screen(body):
    with pytest.raises(Unreadable):
        read_page(body)


def test_zero_and_no_list_is_a_real_empty_answer():
    assert read_page({'totalRecords': 0}) == ([], 0)
    assert run({'totalRecords': 0})[0]['outcome'] == 'no_match'


# --- the answer -------------------------------------------------------------------------


def test_a_clean_screen_says_what_it_covers():
    details, notes = run(page(), full_name='Rowan Vandermeer')
    assert (
        details['outcome'] == 'no_match'
        and details['shape'] == 'screens'
        and details['source_as_of'] == '2026-10-22'
    )
    assert 'federal contracting exclusions and nothing else' in notes and 'clearance' in notes


def test_a_name_hit_is_only_a_possible_match_and_an_npi_can_make_a_match():
    details, notes = run(page(PERSON))
    assert details['outcome'] == 'possible_match' and 'not a finding against them' in notes
    with_npi = {
        **PERSON,
        'exclusionIdentification': {**PERSON['exclusionIdentification'], 'npi': '1104130236'},
    }
    details, _ = run(page(with_npi), npi='1104130236')
    assert details['outcome'] == 'match' and details['matched_on'] == ['full_name', 'npi']


def test_firms_samples_and_dates_are_read():
    assert run(page(FIRM), full_name='Supreme Fuels')[0]['outcome'] == 'possible_match'
    details, notes = run(page(PERSON))
    assert '03-09-2021' in notes and 'HEALTH AND HUMAN SERVICES' in notes
    assert details['hits'][0]['state'] == 'CA'


def test_sams_own_looser_matching_is_not_trusted():
    other = {**PERSON, 'exclusionIdentification': {'firstName': 'TOBIAS', 'lastName': 'VANDERMEER'}}
    details, _ = run(page(other))
    assert details['outcome'] == 'no_match' and details['candidates_returned'] == 1


def test_inactive_unreadable_and_mixed_statuses():
    def with_actions(*actions):
        return {**PERSON, 'exclusionActions': {'listOfActions': list(actions)}}

    assert run(page(with_actions({'recordStatus': 'Inactive'})))[0]['outcome'] == 'no_match'
    assert run(page(with_actions({'recordStatus': ''})))[0]['outcome'] == 'possible_match'
    assert (
        run(page(with_actions({'recordStatus': 'Inactive'}, {'recordStatus': 'Active'})))[0]['outcome']
        == 'possible_match'
    )


def test_the_same_record_from_two_names_is_reported_once():
    details, _ = run([page(PERSON), page(PERSON)], previous=['Amara Okafor'])
    assert details['hit_count'] == 1


def test_input_checks():
    assert SamInput(full_name=' Amara Okafor ').full_name == 'Amara Okafor'
    for bad in (
        {'full_name': ''},
        {'full_name': 'A'},
        {'full_name': 'Amara', 'npi': '123'},
        {'npi': '1104130236'},
    ):
        with pytest.raises(ValueError):
            SamInput(**bad)


# --- asking SAM.gov ----------------------------------------------------------------------


class Job:
    """The parts of a running job that search() uses."""

    def __init__(self, quota=10):
        self.left, self.cache = quota, self._Cache()

    def use_quota(self, name, limit):
        self.left -= 1
        return self.left >= 0

    class _Cache(dict):
        def get(self, question):
            return super().get(json.dumps(question))

        def put(self, question, value, seconds):
            self[json.dumps(question)] = value

    def check_cancelled(self):
        pass


def settings(**kw):
    return SamSettings(api_key=KEY, **kw)


def sam(handler):
    return httpx.MockTransport(handler)


def test_every_page_is_read_and_each_page_counts_against_the_limit():
    asked = []

    def handler(request):
        number = int(request.url.params['page'])
        asked.append(number)
        return httpx.Response(200, json=page(*([PERSON] * (50 if number < 2 else 10)), total=110))

    job = Job(quota=10)
    found, _ = search(job, 'Amara Okafor', settings(cache_hours=0), sam(handler))
    assert len(found) == 110 and asked == [0, 1, 2] and job.left == 7


def test_more_matches_than_can_be_read_is_never_a_partial_answer():
    def handler(request):
        return httpx.Response(200, json=page(*([PERSON] * 50), total=500))

    with pytest.raises(JobFailed) as caught:
        search(Job(), 'Amara Okafor', settings(cache_hours=0, max_pages=2), sam(handler))
    assert caught.value.code == 'source_incomplete'


def test_the_daily_limit_stops_before_calling():
    calls = []
    with pytest.raises(JobFailed) as caught:
        search(
            Job(quota=0),
            'Amara Okafor',
            settings(cache_hours=0),
            sam(lambda r: calls.append(r) or httpx.Response(200)),
        )
    assert caught.value.code == 'daily_limit_reached' and calls == []


@pytest.mark.parametrize(
    'status, code, retry',
    [(503, 'source_unavailable', True), (401, 'not_configured', False), (403, 'not_configured', False),
     (429, 'daily_limit_reached', False), (404, 'source_rejected', False)],
)  # fmt: skip
def test_failures_have_their_own_codes(status, code, retry):
    with pytest.raises(JobFailed) as caught:
        search(Job(), 'Amara Okafor', settings(cache_hours=0), sam(lambda r: httpx.Response(status)))
    assert (caught.value.code, caught.value.retry) == (code, retry)


def test_an_unreadable_answer_fails_closed():
    with pytest.raises(JobFailed) as caught:
        search(
            Job(), 'Amara Okafor', settings(cache_hours=0), sam(lambda r: httpx.Response(200, json={'x': 1}))
        )
    assert caught.value.code == 'source_unreadable'


def test_the_key_never_leaves_the_request(caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=page(PERSON))

    job = Job()
    found, when = search(job, 'Amara Okafor', settings(), sam(handler))
    assert seen[0].url.params['api_key'] == KEY  # sent to SAM.gov
    record, warnings = screen(found, ['Amara Okafor'], '', when, False)
    stored = json.dumps([record, warnings, list(job.cache.values())])
    assert KEY not in stored and 'api_key' not in stored and KEY not in caplog.text

    def down(request):
        raise httpx.ConnectError(f'failed {request.url}')

    with pytest.raises(JobFailed):
        search(Job(), 'Someone Else', settings(cache_hours=0), sam(down))
    assert KEY not in caplog.text
    assert 'SecretStr' in repr(settings()) and KEY not in repr(settings())


def test_a_cached_name_costs_no_call_but_other_wording_is_asked_again():
    calls = []

    def handler(request):
        calls.append(request.url.params['exclusionName'])
        return httpx.Response(200, json=page(PERSON))

    job = Job()
    search(job, 'Amara Okafor', settings(), sam(handler))
    found, _ = search(job, '  amara   OKAFOR ', settings(), sam(handler))  # spaces and case only
    assert len(calls) == 1 and found[0].name == 'AMARA OKAFOR'
    search(job, 'Okafor Amara', settings(), sam(handler))  # SAM.gov may answer this differently
    assert len(calls) == 2


# --- review findings: no false clearance --------------------------------------------------


def test_a_full_page_without_a_total_reads_the_next_page():
    other = {**PERSON, 'exclusionIdentification': {'firstName': 'TOBIAS', 'lastName': 'VANDERMEER'}}

    def handler(request):
        if request.url.params['page'] == '0':
            return httpx.Response(200, json={'excludedEntity': [other] * 50})  # no totalRecords
        return httpx.Response(200, json={'excludedEntity': [PERSON]})

    found, _ = search(Job(), 'Amara Okafor', settings(cache_hours=0), sam(handler))
    record, _ = screen(found, ['Amara Okafor'], '', NOW, False)
    assert len(found) == 51 and record['details']['outcome'] == 'possible_match'


def test_a_total_written_with_a_comma_is_read():
    assert read_page({'totalRecords': '1,000', 'excludedEntity': [PERSON]})[1] == 1000


def test_a_former_name_never_hides_a_record_with_a_middle_name():
    jane = {
        **PERSON,
        'exclusionIdentification': {'firstName': 'JANE', 'middleName': 'MARIE', 'lastName': 'SMITH'},
    }
    details, _ = run(page(jane), full_name='Jane Doe', previous=['Jane Smith'])
    assert details['outcome'] == 'possible_match'


def test_an_inactive_copy_never_hides_an_active_one():
    inactive = {
        **PERSON,
        'exclusionActions': {'listOfActions': [{'activateDate': '03-09-2021', 'recordStatus': 'Inactive'}]},
    }
    details, _ = run(page(inactive, PERSON))
    assert details['outcome'] == 'possible_match'


def test_two_records_differing_only_in_npi_are_both_kept():
    blank = {**PERSON, 'exclusionIdentification': {**PERSON['exclusionIdentification'], 'npi': '1999999999'}}
    with_npi = {
        **PERSON,
        'exclusionIdentification': {**PERSON['exclusionIdentification'], 'npi': '1104130236'},
    }
    details, _ = run(page(blank, with_npi), full_name='Someone Else', npi='1104130236')
    assert details['outcome'] == 'match' and details['matched_on'] == ['npi']


def test_a_very_long_former_name_is_refused():
    with pytest.raises(ValueError):
        SamInput(full_name='Amara Okafor', previous_names=['x' * 201])


def test_the_unverified_mapping_is_said_in_every_answer():
    details, notes = run(page(), full_name='Rowan Vandermeer')
    assert details['mapping_verified'] is False and 'not yet been checked' in notes


# --- through the service --------------------------------------------------------------------


def test_sam_through_the_service(tmp_path, monkeypatch):
    from agents_service.agents.sam import agent as sam_agent

    calls = []

    def fake_get_json(url, **kwargs):
        calls.append(kwargs['params']['exclusionName'])
        return page(PERSON) if 'amara' in kwargs['params']['exclusionName'] else page()

    monkeypatch.setattr(sam_agent, 'get_json', fake_get_json)
    services = build(make_settings(tmp_path, enabled_agents='cv,sam'))
    services.db.upgrade()
    client = TestClient(create_app(services))
    assert client.get('/health/ready').json()['agents']['sam'] == 'needs setup'
    token = create_key(services.db, services.settings.key_pepper, 'kormic', 'sam', 'sam')[1]
    state = next(
        a['state']
        for a in client.get('/v1/agents', headers=auth(token)).json()['agents']
        if a['name'] == 'sam'
    )
    assert state == 'not configured: AGENTS_SAM_API_KEY'
    job = client.post(
        '/v1/sam/jobs', headers=auth(token), json={'data': {'full_name': 'Amara Okafor'}}
    ).json()
    runner.run(services, once=True)
    assert client.get(job['links']['self'], headers=auth(token)).json()['error']['code'] == 'not_configured'

    services.settings.agent_settings['sam'] = {'api_key': KEY, 'daily_limit': 2}
    assert client.get('/health/ready').json()['agents']['sam'] == 'ok'
    results = []
    for name in ('Amara Okafor', 'Amara Okafor', 'Rowan Vandermeer', 'Kim Lee'):
        job = client.post('/v1/sam/jobs', headers=auth(token), json={'data': {'full_name': name}}).json()
        runner.run(services, once=True)
        results.append(client.get(job['links']['self'], headers=auth(token)).json())
    assert results[0]['result']['records'][0]['value'] == 'possible_match'
    assert results[1]['state'] == 'done'  # cached: no call
    assert results[2]['result']['records'][0]['value'] == 'no_match'
    assert results[3]['error']['code'] == 'daily_limit_reached'  # third call of the day, limit 2
    assert calls == ['amara okafor', 'rowan vandermeer']  # first and last name words only
    assert KEY not in json.dumps(results)


# --- second review: how SAM.gov searches ------------------------------------------------


def test_only_first_and_last_name_words_are_sent():
    from agents_service.agents.sam.agent import search_terms

    assert search_terms('Jane Marie Smith') == 'jane smith'  # SAM.gov ANDs every word sent
    assert search_terms('Dr. Jane Smith Jr.') == 'jane smith'
    assert search_terms('Okafor, Amara') == 'okafor amara'
    assert search_terms('Smith') == 'smith' and search_terms('J. K.') == ''


def test_a_middle_name_never_reaches_sam_and_the_record_is_still_found():
    sent = []

    def handler(request):
        sent.append((request.url.params['exclusionName'], request.url.params['size']))
        return httpx.Response(200, json=page(PERSON))  # listed as AMARA OKAFOR

    found, _ = search(Job(), 'Dr Amara Chidinma Okafor', settings(cache_hours=0), sam(handler))
    record, _ = screen(found, ['Dr Amara Chidinma Okafor'], '', NOW, False)
    assert sent == [('amara okafor', '10')]  # GSA allows 1-10 per page
    assert record['details']['outcome'] == 'possible_match'


def test_one_project_cannot_use_up_sam_for_everyone():
    class ProjectJob(Job):
        project = 'busy'

        def __init__(self):
            super().__init__(quota=100)
            self.counts = {}

        def use_quota(self, name, limit):
            self.counts[name] = self.counts.get(name, 0) + 1
            return self.counts[name] <= limit

    job = ProjectJob()
    config = settings(cache_hours=0, project_daily_limit=2)
    handler = sam(lambda r: httpx.Response(200, json=page()))
    search(job, 'Amara Okafor', config, handler)
    search(job, 'Rowan Vandermeer', config, handler)
    with pytest.raises(JobFailed) as caught:
        search(job, 'Kim Lee', config, handler)
    assert caught.value.code == 'daily_limit_reached' and job.counts['project:busy'] == 3


def listed(first, last, entity=None):
    person = json.loads(json.dumps(PERSON))
    person['exclusionIdentification'].update(
        firstName=first, lastName=last, entityName=entity or f'{first} {last}'
    )
    return person


@pytest.mark.parametrize(
    ('first', 'last', 'searched'),
    [
        ('AMARA', 'OKAFOR', 'Dr. Amara Okafor'),
        ('AMARA', 'OKAFOR JR', 'Amara Okafor'),
        ('AMARA ELIZABETH', 'OKAFOR', 'Amara Grace Okafor'),
        ('AMARA', 'OKAFOR', 'Amara Okafor, LCSW'),
        ('BRIAN', 'OBRIEN', "Brian O'Brien"),
        ('LUIS', 'ABADSANTOS', 'Luis Abad-Santos'),
    ],
)
def test_titles_suffixes_and_middle_names_never_hide_a_listing(first, last, searched):
    details, _ = run(page(listed(first, last)), full_name=searched)
    assert details['outcome'] == 'possible_match'


def test_a_firm_is_found_whatever_its_company_type():
    firm = json.loads(json.dumps(FIRM))
    firm['exclusionIdentification']['entityName'] = 'ACME HEALTH SERVICES INC'
    details, _ = run(page(firm), full_name='Acme Health Services LLC')
    assert details['outcome'] == 'possible_match'


def test_another_first_name_is_not_a_hit():
    details, _ = run(page(listed('JOHN', 'OKAFOR')), full_name='Amara Okafor')
    assert details['outcome'] == 'no_match'


def test_what_is_sent_to_sam_gov():
    from agents_service.agents.sam.agent import search_terms

    assert search_terms('Dr. Jane Marie Smith Jr.') == 'jane smith'
    assert search_terms('Jane Smith, LCSW') == 'jane smith'
    assert search_terms("Brian O'Brien") == 'brian brien'
    assert search_terms('Acme Health Services, Inc.') == 'acme services'
    assert search_terms('Dr. Jr.') == ''


def test_a_name_of_titles_only_is_refused_and_an_empty_limit_is_no_limit():
    for bad in ({'full_name': 'Dr. Jr.'}, {'full_name': 'Amara Okafor', 'previous_names': ['Mrs']}):
        with pytest.raises(ValueError):
            SamInput(**bad)
    assert SamSettings(api_key=KEY, project_daily_limit='').project_daily_limit is None
