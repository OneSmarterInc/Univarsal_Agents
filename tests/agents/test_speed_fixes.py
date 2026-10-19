"""Speed changes for slow CPU models: fewer pieces, and prompts Ollama can reuse."""

from unittest.mock import patch

from agents_service.agents.cv.local_reader import GROUPS, _section_request, extract_local_resume, plan
from agents_service.ai.providers import Completion
from tests.agents.kormic_adapter import CONFIG

ONE_PAGE_CV = '\n'.join(
    ['Alex Example', 'alex@example.com | +1 555 0100 | Columbus, Ohio', 'Summary', 'Data analyst. ' * 20]
    + ['Experience']
    + [
        f'Data Analyst, Example Health, 20{10 + i} - 20{11 + i}. Built reports in SQL and Python for clinics.'
        for i in range(14)
    ]
    + [
        'Education',
        'B.Sc. Computer Science, Example University, 2006 - 2010',
        'Skills',
        'Python, SQL, Tableau',
    ]
    + [
        'Projects',
        'Reporting project: dashboards for nurses. ' * 5,
        'Certifications',
        'Tableau Certified 2020',
    ]
)


def test_bigger_pieces_mean_fewer_questions():
    assert 1800 < len(ONE_PAGE_CV) < 3000
    kormic = plan(ONE_PAGE_CV, limit=1800)
    faster = plan(ONE_PAGE_CV, limit=3000)
    assert len({chunk for _, chunk in faster}) == 1
    assert len({chunk for _, chunk in kormic}) == 2
    assert len(faster) < len(kormic)


def test_education_is_asked_last_for_each_piece():
    for limit in (1800, 3000):
        pending = plan(ONE_PAGE_CV, limit=limit)
        by_chunk = {}
        for fields, chunk in pending:
            by_chunk.setdefault(chunk, []).append(fields)
        for groups in by_chunk.values():
            if ('education',) in groups:
                assert groups[-1] == ('education',)


def test_text_first_gives_every_question_the_same_opening():
    chunk = ONE_PAGE_CV
    requests = [
        _section_request(fields, chunk, text_first=True) for fields in GROUPS if fields != ('education',)
    ]
    systems = {system for _, system, _ in requests}
    assert len(systems) == 1
    openings = {content[: len(chunk) + 9] for _, _, content in requests}
    assert openings == {'CV text:\n' + chunk}
    # Each question still names only its own fields, after the text.
    assert all(content.rstrip().endswith('from the CV text above.') for _, _, content in requests)


def test_kormic_prompt_layout_is_still_available():
    _, system, content = _section_request(('skills', 'languages'), 'text', text_first=False)
    assert system.startswith('Extract only skills, languages')
    assert content == 'text'


def test_reader_uses_the_configured_piece_size_and_layout():
    sent = []

    def generate(**kwargs):
        sent.append(kwargs)
        props = kwargs['schema']['properties']
        empty = {k: ([] if v.get('type') == 'array' or 'anyOf' not in v else None) for k, v in props.items()}
        return Completion(data=empty)

    config = dict(CONFIG, chunk_chars=3000, text_first=True)
    with (
        patch('agents_service.agents.cv.local_reader.extract_owner', return_value=[]),
        patch('agents_service.agents.cv.local_reader.provider') as factory,
    ):
        factory.return_value.__enter__.return_value.generate.side_effect = generate
        extract_local_resume(ONE_PAGE_CV, config)
    # Education uses its own numbered-line prompt; every other section uses text first.
    sections = [
        r for r in sent if r['name'] == 'resume_section' and 'education' not in r['schema']['properties']
    ]
    assert sections and all(r['messages'][0]['content'].startswith('CV text:\n') for r in sections)
    assert len({r['system'] for r in sections}) == 1
