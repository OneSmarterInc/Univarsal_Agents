"""NPPES agent: ported from kormic-agents' tests, in this service's record format."""

import httpx
import pytest
from fastapi.testclient import TestClient

from agents_service.agents.base import JobFailed
from agents_service.agents.nppes import agent as nppes
from agents_service.agents.nppes.agent import NppesInput, NppesSettings, ask_cms, read_record
from agents_service.agents.nppes.record import check_digit_ok, parse_npi, primary_taxonomy
from agents_service.api.app import create_app
from agents_service.core.keys import create_key
from agents_service.services import build
from agents_service.worker import runner
from tests.conftest import auth, make_settings

VALID_NPI = '1234567893'
NOW = '2026-10-22T10:00:00+00:00'
RECORD = {
    'enumeration_type': 'NPI-1',
    'number': VALID_NPI,
    'basic': {
        'first_name': 'Amara', 'middle_name': 'Chidinma', 'last_name': 'Okafor', 'credential': 'RN, BSN',
        'status': 'A', 'enumeration_date': '2016-04-11', 'last_updated': '2025-02-03',
    },
    'taxonomies': [
        {'code': '163WA0400X', 'desc': 'Registered Nurse', 'primary': True, 'state': 'CA', 'license': 'A12345'},
        {'code': '163WC0400X', 'desc': 'Critical Care', 'primary': False, 'state': 'CA'},
    ],
    'addresses': [
        {'address_purpose': 'MAILING', 'state': 'NV', 'city': 'Reno'},
        {'address_purpose': 'LOCATION', 'state': 'CA', 'city': 'Oakland'},
    ],
}  # fmt: skip


def read(record=RECORD, body=None):
    records, warnings = read_record(VALID_NPI, body or {'result_count': 1, 'results': [record]}, NOW)
    by = {}
    for r in records:
        by.setdefault(r['section'], []).append(r)
    return by, warnings


# --- the number -----------------------------------------------------------------


def test_the_check_digit_catches_a_transposition():
    assert check_digit_ok(VALID_NPI)
    assert not check_digit_ok('1234567839') and not check_digit_ok('1111111111')


def test_an_npi_is_read_out_of_whatever_was_pasted():
    assert parse_npi(' 1234-567-893 ') == VALID_NPI
    assert parse_npi('A12345') == '' and parse_npi(None) == ''


@pytest.mark.parametrize('bad', ['1234567839', '', '12345', 'A12345', '1234 5678 93 1'])
def test_a_bad_npi_is_refused_before_anything_is_sent(bad):
    with pytest.raises(ValueError):
        NppesInput(npi=bad)
    assert NppesInput(npi='1234-567-893').npi == VALID_NPI


# --- what CMS establishes ---------------------------------------------------------


def test_the_registry_confirms_the_number_and_the_name():
    by, _ = read()
    assert by['npi'][0]['details'] == {
        'npi': VALID_NPI,
        'found': True,
        'last_updated': '2025-02-03',
        'method': 'primary_source',
    }
    assert by['full_name'][0]['value'] == 'Amara Chidinma Okafor'
    assert by['full_name'][0]['details']['method'] == 'primary_source'
    assert by['npi_status'][0]['value'] == 'active'
    assert by['npi_enumerated_on'][0]['value'] == '2016-04-11'
    assert by['npi'][0]['sources'][0] == {
        'source': f'https://npiregistry.cms.hhs.gov/provider-view/{VALID_NPI}',
        'retrieved_at': NOW,
        'evidence': '',
    }


def test_a_number_cms_does_not_have_is_an_answer_not_a_failure():
    by, warnings = read(body={'result_count': 0, 'results': []})
    assert by['npi'][0]['details'] == {'npi': VALID_NPI, 'found': False, 'method': 'primary_source'}
    assert list(by) == ['npi'] and 'no record' in warnings[0]


def test_two_records_for_one_npi_are_not_chosen_between():
    by, _ = read(body={'results': [RECORD, RECORD]})
    assert by['npi'][0]['details']['found'] is False


# --- what the provider only typed in --------------------------------------------------


def test_self_reported_fields_are_never_primary_source():
    by, warnings = read()
    for section in ('credential', 'taxonomy', 'practice_state', 'claimed_registration'):
        assert all(r['details']['method'] == 'self_attested' for r in by[section]), section
    claimed = by['claimed_registration'][0]['details']
    assert (claimed['number'], claimed['jurisdiction'], claimed['kind']) == ('A12345', 'US-CA', 'licence')
    assert any('none has been confirmed with a state board' in w for w in warnings)


def test_every_state_licence_is_returned_once():
    record = {**RECORD, 'taxonomies': [
        {'desc': 'NP', 'license': 'RN124410', 'state': 'AZ'},
        {'desc': 'NP', 'license': 'NP95027144', 'state': 'CA'},
        {'desc': 'NP', 'license': 'AP3827', 'primary': True, 'state': 'AZ'},
        {'desc': 'ER', 'license': 'ap3827', 'state': 'az'},
    ]}  # fmt: skip
    by, warnings = read(record)
    found = {(r['details']['jurisdiction'], r['details']['number']) for r in by['claimed_registration']}
    assert found == {('US-AZ', 'RN124410'), ('US-CA', 'NP95027144'), ('US-AZ', 'AP3827')}
    assert any('3 licence numbers' in w for w in warnings)


def test_primary_taxonomy_and_practice_state():
    by, _ = read()
    assert by['taxonomy'][0]['value'] == 'Registered Nurse'
    assert by['practice_state'][0]['value'] == 'CA'  # not the mailing state
    assert primary_taxonomy({'taxonomies': [{'desc': 'A'}, {'desc': 'B'}]})['desc'] == 'A'
    assert primary_taxonomy({}) == {}


def test_former_names_are_returned_for_the_project_to_compare():
    record = {**RECORD, 'other_names': [{'first_name': 'Amara', 'last_name': 'Smith', 'type': 'Former Name'}]}
    by, _ = read(record)
    assert by['former_name'][0]['value'] == 'Amara Smith'
    assert by['former_name'][0]['details']['method'] == 'self_attested'


def test_an_organisation_and_a_deactivated_npi_are_called_out():
    record = {**RECORD, 'enumeration_type': 'NPI-2', 'basic': {**RECORD['basic'], 'organization_name': 'Oakland Clinic',
              'status': 'D', 'deactivation_date': '2024-01-09'}}  # fmt: skip
    by, warnings = read(record)
    assert by['npi_type'][0]['value'] == 'organisation' and by['full_name'][0]['value'] == 'Oakland Clinic'
    assert by['npi_status'][0]['details'] == {
        'value': 'deactivated',
        'deactivated_on': '2024-01-09',
        'method': 'primary_source',
    }
    assert any('organisation' in w for w in warnings) and any(
        'deactivated on 2024-01-09' in w for w in warnings
    )


def test_a_status_that_cannot_be_read_is_not_active():
    by, _ = read({**RECORD, 'basic': {**RECORD['basic'], 'status': ''}})
    assert by['npi_status'][0]['value'] == 'deactivated'


# --- asking CMS ---------------------------------------------------------------------


def cms(handler):
    return lambda: ask_cms(VALID_NPI, NppesSettings(), transport=httpx.MockTransport(handler))


def test_cms_is_asked_for_the_exact_number_only():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={'results': [RECORD]})

    assert cms(handler)()['results'][0]['number'] == VALID_NPI
    assert seen[0].host == 'npiregistry.cms.hhs.gov'
    assert dict(seen[0].params) == {'version': '2.1', 'number': VALID_NPI}


def test_cms_being_down_is_retried_and_a_refusal_is_not():
    with pytest.raises(JobFailed) as down:
        cms(lambda r: httpx.Response(503))()
    assert (down.value.code, down.value.retry) == ('source_unavailable', True)
    with pytest.raises(JobFailed) as refused:
        cms(lambda r: httpx.Response(400))()
    assert (refused.value.code, refused.value.retry) == ('source_rejected', False)


def test_an_errors_answer_is_never_read_as_no_such_npi():
    with pytest.raises(JobFailed) as caught:
        cms(lambda r: httpx.Response(200, json={'Errors': [{'description': 'bad'}]}))()
    assert caught.value.code == 'source_rejected'


# --- through the service ------------------------------------------------------------


@pytest.fixture
def nppes_services(tmp_path, monkeypatch):
    services = build(make_settings(tmp_path, enabled_agents='cv,nppes'))
    services.db.upgrade()
    calls = []

    def fake_get_json(url, **kwargs):
        calls.append(kwargs['params'])
        return {'result_count': 1, 'results': [RECORD]}

    monkeypatch.setattr(nppes, 'get_json', fake_get_json)
    return services, calls


def test_nppes_through_the_api_and_worker(nppes_services):
    services, calls = nppes_services
    client = TestClient(create_app(services))
    token = create_key(services.db, services.settings.key_pepper, 'kormic', 'nppes', 'nppes')[1]
    assert (
        client.post('/v1/nppes/jobs', headers=auth(token), json={'data': {'npi': '1234567839'}}).status_code
        == 422
    )
    job = client.post('/v1/nppes/jobs', headers=auth(token), json={'data': {'npi': '1234-567-893'}}).json()
    runner.run(services, once=True)
    done = client.get(job['links']['self'], headers=auth(token)).json()
    assert done['state'] == 'done' and done['result']['model'] is None
    assert {r['section'] for r in done['result']['records']} >= {'npi', 'full_name', 'claimed_registration'}
    # The same NPI again is answered from the cache: CMS is asked once.
    again = client.post('/v1/nppes/jobs', headers=auth(token), json={'data': {'npi': VALID_NPI}}).json()
    runner.run(services, once=True)
    assert client.get(again['links']['self'], headers=auth(token)).json()['state'] == 'done'
    assert len(calls) == 1
    assert client.get('/health/ready').json()['agents']['nppes'] == 'ok'  # needs no key


def test_nppes_settings_have_safe_defaults():
    config = NppesSettings()
    assert config.api_url.startswith('https://') and config.cache_hours == 24
    with pytest.raises(ValueError):
        NppesSettings(timeout_seconds=0)
