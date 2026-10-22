"""OIG agent: ported from kormic-agents' tests, in this service's record format."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from agents_service.agents.oig import agent as oig
from agents_service.agents.oig.agent import OigInput, screen
from agents_service.agents.oig.leie import Exclusion, read_csv, row_to_exclusion
from agents_service.api.app import create_app
from agents_service.core.keys import create_key
from agents_service.core.references import References
from agents_service.services import build
from agents_service.worker import runner
from tests.conftest import auth, make_settings

AUGUST = '2026-08-01'
HEADER = (
    'LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,NPI,DOB,STATE,EXCLTYPE,EXCLDATE,REINDATE,WVRSTATE\n'
)
CSV = HEADER + 'OKAFOR,AMARA,C,,NURSE,REGISTERED NURSE,0000000000,00000000,CA,1128b4,20210309,00000000,\n'


def excluded(**kw):
    base = dict(
        last_name='Okafor',
        first_name='Amara',
        exclusion_type='1128b4',
        excluded_on=date(2021, 3, 9),
        state='CA',
    )
    return Exclusion(**{**base, **kw})


def run(records=None, full_name=None, previous_names=(), dob=None, npi=''):
    aliases = [n for n in [*previous_names, full_name or ''] if n]
    record, warnings = screen([excluded()] if records is None else records, aliases, dob, npi, AUGUST)
    return record['details'], ' '.join(warnings)


# --- the answer --------------------------------------------------------------------


def test_the_answer_is_a_screen_with_what_bounds_it():
    details, _ = run(full_name='Rowan Vandermeer', dob=date(1984, 2, 11))
    assert details['shape'] == 'screens' and details['method'] == 'primary_source'
    assert details['source_as_of'] == AUGUST and 'date_of_birth' in details['matched_on']


def test_a_hit_reports_what_corroborated_it_not_what_was_asked():
    details, _ = run(full_name='Amara Okafor', dob=date(1984, 2, 11))
    assert details['outcome'] == 'possible_match' and details['matched_on'] == ['full_name']
    assert 'date_of_birth' in details['searched_on']


def test_a_clean_screen_is_never_worded_as_a_clearance():
    details, notes = run(full_name='Rowan Vandermeer', dob=date(1990, 1, 1))
    assert details['outcome'] == 'no_match' and 'not a clearance' in notes and AUGUST in notes
    for word in ('cleared', 'verified', 'credentialed'):
        assert word not in notes.lower()


def test_nothing_to_screen_on_is_refused():
    for data in ({}, {'full_name': '  '}, {'previous_names': ['']}):
        with pytest.raises(ValueError):
            OigInput(**data)
    assert OigInput(npi='1104-130-236').npi == '1104130236'


# --- identity -------------------------------------------------------------------------


def test_a_name_only_hit_is_only_a_possible_match():
    details, notes = run(full_name='Amara Okafor')
    assert details['outcome'] == 'possible_match' and 'not a finding against them' in notes


def test_a_matching_date_of_birth_makes_it_a_match():
    details, _ = run([excluded(date_of_birth=date(1984, 2, 11))], 'Amara Okafor', dob=date(1984, 2, 11))
    assert details['outcome'] == 'match' and details['matched_on'] == ['date_of_birth', 'full_name']


def test_a_disagreeing_date_of_birth_clears_the_record():
    details, _ = run([excluded(date_of_birth=date(1955, 7, 30))], 'Amara Okafor', dob=date(1984, 2, 11))
    assert details['outcome'] == 'no_match'


def test_an_npi_hit_stands_on_its_own_and_says_so():
    records = [excluded(last_name='Different', first_name='Person', npi='1104130236')]
    details, _ = run(records, 'Amara Okafor', npi='1104130236')
    assert details['outcome'] == 'match'
    # Fixed from the original: the name did not match, so it is not listed as corroborating.
    assert details['matched_on'] == ['npi']


def test_the_blank_npi_in_the_file_matches_nobody():
    assert row_to_exclusion({'LASTNAME': 'X', 'FIRSTNAME': 'Y', 'NPI': '0000000000'}).npi == ''
    assert row_to_exclusion({'LASTNAME': 'X', 'FIRSTNAME': 'Y', 'NPI': '0'}).npi == ''  # the real file's form
    details, _ = run([excluded(last_name='Nobody', first_name='Relevant')], 'Amara Okafor', npi='0000000000')
    assert details['outcome'] == 'no_match'


def test_a_former_name_is_screened_too():
    records = [excluded(last_name='Smith', first_name='Shelley')]
    assert run(records, 'Shelley Akey')[0]['outcome'] == 'no_match'
    details, _ = run(records, 'Shelley Akey', previous_names=['Shelley Smith'])
    assert details['outcome'] == 'possible_match' and 'previous_names' in details['searched_on']


def test_reinstated_order_and_middle_names():
    assert run([excluded(reinstated_on=date(2024, 6, 1))], 'Amara Okafor')[0]['outcome'] == 'no_match'
    assert run(full_name='Okafor, Amara')[0]['outcome'] == 'possible_match'
    assert run(full_name='Amara Chidinma Okafor')[0]['outcome'] == 'possible_match'


def test_an_unknown_birth_date_in_the_file_does_not_clear_a_record():
    record = row_to_exclusion(
        {'LASTNAME': 'Okafor', 'FIRSTNAME': 'Amara', 'DOB': '00000000', 'EXCLTYPE': '1128b4'}
    )
    assert run([record], 'Amara Okafor', dob=date(1984, 2, 11))[0]['outcome'] == 'possible_match'


# --- what a reviewer is told ------------------------------------------------------------


def test_reasons_waivers_and_weak_screens_are_explained():
    assert 'licence was revoked' in run(full_name='Amara Okafor')[1]
    assert '9999z9' in run([excluded(exclusion_type='9999z9')], 'Amara Okafor')[1]
    assert 'waiver' in run([excluded(waiver_state='CA')], 'Amara Okafor')[1].lower()
    assert 'weak in both directions' in run(full_name='Rowan Vandermeer')[1]


# --- the file ------------------------------------------------------------------------------


def test_a_leie_row_becomes_an_exclusion():
    record = row_to_exclusion({
        'LASTNAME': 'OKAFOR', 'FIRSTNAME': 'AMARA', 'NPI': '1104130236', 'DOB': '19840211', 'STATE': 'CA',
        'EXCLTYPE': '1128b4', 'EXCLDATE': '20210309', 'REINDATE': '00000000',
    })  # fmt: skip
    assert (record.date_of_birth, record.excluded_on, record.reinstated_on) == (
        date(1984, 2, 11),
        date(2021, 3, 9),
        None,
    )


def test_a_file_that_is_not_the_leie_is_refused():
    with pytest.raises(ValueError, match='missing columns'):
        read_csv(b'name,dob\nA,1\n')
    with pytest.raises(ValueError, match='no rows'):
        read_csv(HEADER.encode())
    assert len(read_csv(('﻿' + CSV).encode())) == 1  # the byte-order mark OIG files carry


# --- through the service --------------------------------------------------------------------


@pytest.fixture
def oig_services(tmp_path, monkeypatch):
    monkeypatch.setitem(oig._LOADED, 'sha256', None)
    monkeypatch.setitem(oig._LOADED, 'info', None)
    services = build(make_settings(tmp_path, enabled_agents='cv,oig'))
    services.db.upgrade()
    return services


def test_oig_needs_a_loaded_list_then_screens_through_the_service(
    oig_services, tmp_path, monkeypatch, capsys
):
    from agents_service.cli import main as cli

    services = oig_services
    client = TestClient(create_app(services))
    token = create_key(services.db, services.settings.key_pepper, 'kormic', 'oig', 'oig')[1]
    assert client.get('/health/ready').json()['agents']['oig'] == 'needs setup'
    state = next(
        a['state']
        for a in client.get('/v1/agents', headers=auth(token)).json()['agents']
        if a['name'] == 'oig'
    )
    assert state.startswith('no exclusion list loaded')
    job = client.post(
        '/v1/oig/jobs', headers=auth(token), json={'data': {'full_name': 'Amara Okafor'}}
    ).json()
    runner.run(services, once=True)
    assert client.get(job['links']['self'], headers=auth(token)).json()['error']['code'] == 'not_configured'

    monkeypatch.setattr('agents_service.services.build', lambda *a: services)
    leie = tmp_path / 'UPDATED.csv'
    leie.write_text(CSV)
    assert cli.main(['tool', 'oig', 'load', '--file', str(leie), '--as-of', '2026-10-01']) == 0
    assert 'Loaded 1 exclusions published 2026-10-01' in capsys.readouterr().out
    assert client.get('/health/ready').json()['agents']['oig'] == 'ok'

    job = client.post(
        '/v1/oig/jobs',
        headers=auth(token),
        json={'data': {'full_name': 'Amara Okafor', 'date_of_birth': '1984-02-11'}},
    ).json()
    runner.run(services, once=True)
    done = client.get(job['links']['self'], headers=auth(token)).json()
    assert done['state'] == 'done'
    record = done['result']['records'][0]
    assert record['section'] == 'oig_exclusion' and record['value'] == 'possible_match'
    assert record['sources'][0]['retrieved_at'] == '2026-10-01'
    assert record['details']['hits'][0]['reason'] == 'Their licence was revoked, suspended, or surrendered'
    codes = [n['code'] for n in done['result']['notices']]
    assert 'check_possible_match' in codes and 'excluded' not in codes
    assert all(isinstance(text, str) for text in done['result']['warnings'])


def test_a_new_list_replaces_the_old_one_in_every_worker(oig_services):
    services = oig_services
    refs = References(services.db, services.storage, 'oig')
    refs.put('leie', CSV.encode(), '.csv', '2026-09-01', {'rows': 1})
    principal = __import__('agents_service.core.keys', fromlist=['Principal']).Principal(
        'key_k000000000000000', 'k', 'oig'
    )
    services.jobs.create(principal, 'oig', data={'full_name': 'Amara Okafor'})
    runner.run(services, once=True)
    refs.put(
        'leie', HEADER.encode() + b'SMITH,SHELLEY,,,,,,,,1128b4,20200101,00000000,\n', '.csv', '2026-10-01'
    )
    job = services.jobs.create(principal, 'oig', data={'full_name': 'Amara Okafor'})
    runner.run(services, once=True)
    assert services.jobs.get(principal, job['id'])['result']['records'][0]['value'] == 'no_match'


def test_bad_load_commands_are_refused(oig_services, tmp_path, monkeypatch, capsys):
    from agents_service.cli import main as cli

    monkeypatch.setattr('agents_service.services.build', lambda *a: oig_services)
    leie = tmp_path / 'x.csv'
    leie.write_text(CSV)
    assert cli.main(['tool', 'oig', 'load', '--file', str(leie), '--as-of', '01/10/2026']) == 2
    assert cli.main(['tool', 'oig', 'load', '--file', str(leie), '--as-of', '2999-01-01']) == 2
    assert (
        cli.main(['tool', 'oig', 'load', '--file', str(tmp_path / 'missing.csv'), '--as-of', '2026-10-01'])
        == 2
    )
    (tmp_path / 'other.csv').write_text('a,b\n1,2\n')
    assert (
        cli.main(['tool', 'oig', 'load', '--file', str(tmp_path / 'other.csv'), '--as-of', '2026-10-01']) == 2
    )
    assert cli.main(['tool', 'oig', 'status']) == 1


def test_an_old_list_still_screens_with_a_warning(oig_services):
    services = oig_services
    References(services.db, services.storage, 'oig').put('leie', CSV.encode(), '.csv', '2026-01-01')
    from agents_service.core.keys import Principal

    principal = Principal('key_k000000000000000', 'k', 'oig')
    job = services.jobs.create(principal, 'oig', data={'full_name': 'Rowan Vandermeer'})
    runner.run(services, once=True)
    result = services.jobs.get(principal, job['id'])['result']
    assert result['records'][0]['value'] == 'no_match'
    assert any('days old' in w for w in result['warnings'])


def test_the_list_and_its_date_come_from_the_same_file(oig_services, monkeypatch):
    """A new list loaded between a worker's two reads never mixes the new rows with the old date."""
    from agents_service.core.keys import Principal

    services = oig_services
    refs = References(services.db, services.storage, 'oig')
    old = refs.put('leie', CSV.encode(), '.csv', '2026-09-01')
    refs.put(
        'leie', HEADER.encode() + b'SMITH,SHELLEY,,,,,,,,1128b4,20200101,00000000,\n', '.csv', '2026-10-01'
    )
    monkeypatch.setattr(References, 'info', lambda self, name: old)  # the worker saw the old one first
    principal = Principal('key_k000000000000000', 'k', 'oig')
    job = services.jobs.create(principal, 'oig', data={'full_name': 'Amara Okafor'})
    runner.run(services, once=True)
    monkeypatch.undo()
    record = services.jobs.get(principal, job['id'])['result']['records'][0]
    assert (record['value'], record['details']['source_as_of']) == ('no_match', '2026-10-01')


# --- second review --------------------------------------------------------------------------


def test_a_listed_initial_or_missing_first_name_is_still_found():
    assert run([excluded(last_name='Doe', first_name='J')], 'John Doe')[0]['outcome'] == 'possible_match'
    assert run([excluded(last_name='Doe', first_name='J.')], 'J Doe')[0]['outcome'] == 'possible_match'
    assert run([excluded(last_name='Doe', first_name='')], 'John Doe')[0]['outcome'] == 'possible_match'
    assert run([excluded(last_name='Doe', first_name='John')], 'J Doe')[0]['outcome'] == 'possible_match'
    assert run([excluded(last_name='Doe', first_name='Kate')], 'John Doe')[0]['outcome'] == 'no_match'
    assert run([excluded(last_name='Doe', first_name='K')], 'John Doe')[0]['outcome'] == 'no_match'


def test_a_name_that_cannot_be_searched_is_refused():
    for bad in ('Smith', 'J K', 'J'):
        with pytest.raises(ValueError):
            OigInput(full_name=bad)
    with pytest.raises(ValueError):
        OigInput(full_name='Amara Okafor', previous_names=['Smith'])
    assert OigInput(full_name='J Doe').full_name == 'J Doe'
    assert OigInput(npi='1104130236').full_name is None


def test_reloading_the_same_file_with_a_corrected_date_is_used(oig_services):
    from agents_service.core.keys import Principal

    services = oig_services
    refs = References(services.db, services.storage, 'oig')
    principal = Principal('key_k000000000000000', 'k', 'oig')
    refs.put('leie', CSV.encode(), '.csv', '2020-01-01')  # wrong date given
    services.jobs.create(principal, 'oig', data={'full_name': 'Amara Okafor'})
    runner.run(services, once=True)
    refs.put('leie', CSV.encode(), '.csv', '2026-10-01')  # same file, corrected date
    job = services.jobs.create(principal, 'oig', data={'full_name': 'Amara Okafor'})
    runner.run(services, once=True)
    result = services.jobs.get(principal, job['id'])['result']
    assert result['records'][0]['details']['source_as_of'] == '2026-10-01'
    assert not any('days old' in w for w in result['warnings'])


def test_a_firm_matches_only_on_the_whole_name():
    firm = Exclusion(business_name='BRIAN H JENKINS, D D S, P A', exclusion_type='1128b4')
    assert run([firm], 'Brian Aalbers')[0]['outcome'] == 'no_match'  # sharing "Brian" is not enough
    assert run([firm], 'Brian Jenkins')[0]['outcome'] == 'possible_match'  # his practice
    clinic = Exclusion(business_name='Sunrise Home Health Inc', exclusion_type='1128a1')
    assert run([clinic], 'Sunrise Home')[0]['outcome'] == 'possible_match'


def test_apostrophes_and_hyphens_match_however_the_list_writes_them():
    details, _ = run([excluded(last_name='OBRIEN', first_name='BRIAN')], full_name="Brian O'Brien")
    assert details['outcome'] == 'possible_match'
    details, _ = run([excluded(last_name="O'BRIEN", first_name='BRIAN')], full_name='Brian OBrien')
    assert details['outcome'] == 'possible_match'
    details, _ = run([excluded(last_name='ABADSANTOS', first_name='LUIS')], full_name='Luis Abad-Santos')
    assert details['outcome'] == 'possible_match'


def test_a_one_letter_last_name_is_found():
    details, _ = run([excluded(last_name='I', first_name='CHEA')], full_name='Chea I')
    assert details['outcome'] == 'possible_match'
    details, _ = run([excluded(last_name='I', first_name='CHEA')], full_name='Chea Lim')
    assert details['outcome'] == 'no_match'


def test_oig_notices_have_their_own_codes():
    from agents_service.agents.records import notice_for

    _, _ = run(full_name='Amara Okafor')
    record, notes = screen([excluded()], ['Amara Okafor'], None, '', AUGUST)
    assert [notice_for(n)['code'] for n in notes] == ['listed_record', 'check_possible_match']
    record, notes = screen([excluded()], ['Zorvan Quillatree'], None, '', AUGUST)
    assert [notice_for(n)['code'] for n in notes] == ['not_a_clearance', 'name_only_search']
