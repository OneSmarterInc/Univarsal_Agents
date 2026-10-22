"""The accuracy harness itself (the real check needs the real model: `agents accuracy`)."""

import json
from pathlib import Path

from agents_service.cli.accuracy import run, score_case

CASES = Path(__file__).resolve().parents[2] / 'accuracy' / 'cases.json'


def perfect(agent, text, config):
    case = next(c for c in json.loads(CASES.read_text(encoding='utf-8'))['cases'] if c['text'] == text)
    return [{'section': s, 'value': ' · '.join(v)} for s, v in case['expect'].items()]


def test_every_case_is_well_formed():
    cases = json.loads(CASES.read_text(encoding='utf-8'))['cases']
    assert {c['agent'] for c in cases} == {'cv', 'linkedin'}
    for case in cases:
        for values in case['expect'].values():
            for value in values:
                assert value in case['text'], (case['id'], value)


def test_perfect_answers_score_100(settings):
    report = run(settings, cases_path=CASES, ask=perfect)
    assert report['score_percent'] == 100.0 and report['clean']


def test_missing_and_wrong_answers_are_reported():
    case = {'expect': {'name': ['Daniel Okafor']}, 'must_not': {'name': ['Sarah Mills']}}
    found, expected, problems = score_case(case, [{'section': 'name', 'value': 'Sarah Mills'}])
    assert (found, expected) == (0, 1)
    assert problems == ['missing name: Daniel Okafor', 'wrong name: Sarah Mills']
