"""`agents accuracy`: run made-up documents through the real model and check the answers.

This measures the configured model and reader together. Run it after changing
the model, its settings, or a reader, and compare the score with the last run.
"""

import json
import time
from pathlib import Path

from agents_service.agents.base import InputFile
from agents_service.agents.records import add_records
from agents_service.ai.providers import model_config

DEFAULT_CASES = Path('accuracy/cases.json')


def facts_from_text(agent_name, text, config):
    """The checked facts the agent finds in this text (no file reading)."""
    warnings = []
    if agent_name == 'cv':
        from agents_service.agents.cv.agent import ask_model, checked_facts

        data, _ = checked_facts(ask_model(text, config), text, warnings)
    elif agent_name == 'linkedin':
        from agents_service.agents.linkedin.agent import ask_model
        from agents_service.agents.linkedin.schemas import ImageObservation
        from agents_service.agents.linkedin.validation import validate_data

        data = validate_data(
            ImageObservation.model_validate(ask_model(text, config), extra='ignore').model_dump(), text
        )
    else:
        raise ValueError(f'No accuracy reader for agent {agent_name}')
    records = []
    add_records(records, data, InputFile(0, 'case', Path('case'), 'case'))
    return records


def score_case(case, records):
    """(found, expected, problems) for one case."""
    found, expected, problems = 0, 0, []
    for section, values in case.get('expect', {}).items():
        texts = [r['value'].casefold() for r in records if r['section'] == section]
        for value in values:
            expected += 1
            if any(value.casefold() in t for t in texts):
                found += 1
            else:
                problems.append(f'missing {section}: {value}')
    for section, values in case.get('must_not', {}).items():
        texts = [r['value'].casefold() for r in records if r['section'] == section]
        for value in values:
            if any(value.casefold() in t for t in texts):
                problems.append(f'wrong {section}: {value}')
    return found, expected, problems


def run(settings, agent=None, cases_path=DEFAULT_CASES, ask=facts_from_text):
    cases = json.loads(Path(cases_path).read_text(encoding='utf-8'))['cases']
    if agent:
        cases = [c for c in cases if c['agent'] == agent]
    total_found = total_expected = 0
    report = []
    for case in cases:
        started = time.monotonic()
        try:
            records = ask(case['agent'], case['text'], model_config(settings, case['agent']))
            found, expected, problems = score_case(case, records)
        except Exception as exc:  # noqa: BLE001 - a failing case is reported, not raised
            found, expected, problems = (
                0,
                sum(len(v) for v in case.get('expect', {}).values()),
                [type(exc).__name__],
            )
        total_found += found
        total_expected += expected
        report.append(
            {
                'case': case['id'],
                'found': f'{found}/{expected}',
                'seconds': round(time.monotonic() - started, 1),
                'problems': problems,
            }
        )
    score = round(100 * total_found / total_expected, 1) if total_expected else 0.0
    return {'score_percent': score, 'cases': report, 'clean': all(not r['problems'] for r in report)}
