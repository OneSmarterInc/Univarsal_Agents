"""Clean-up of checked CV facts (version 1.3.2), with made-up people: repeats merged,
a project filed again as a job dropped, a summary that is a block of the CV refused,
and skill lines split into skills."""

from agents_service.agents.cv.agent import checked_facts
from agents_service.agents.cv.cleanup import (
    clean_summary,
    clean_summary_variants,
    drop_projects_filed_as_jobs,
    merge_repeats,
    split_skills,
)


def job(title, organization=None, start=None, end=None, description=None, evidence=''):
    return {
        'title': title,
        'organization': organization,
        'start_date': start,
        'end_date': end,
        'description': description,
        'technologies': [],
        'url': None,
        'evidence': evidence or title,
    }


def test_the_same_job_twice_keeps_the_one_with_its_bullets():
    bare = job(
        'Web Intern',
        'Example Academy',
        'Aug 2025',
        'Nov 2025',
        evidence='Web Intern | Example Academy Aug 2025',
    )
    full = job(
        'Web Intern',
        'Example Academy',
        'Aug 2025',
        'Nov 2025',
        description='● Built an example site.',
        evidence='Web Intern | Example Academy Aug 2025\n● Built an example site.',
    )
    assert merge_repeats([bare, full]) == [full]
    assert merge_repeats([full, bare]) == [full]
    other = job('Data Intern', 'Example Labs', '2026')
    assert merge_repeats([full, other]) == [full, other]


def test_a_project_filed_again_as_a_job_stays_a_project():
    projects = [
        job(
            'Example-Code',
            None,
            'Jul 2026',
            'Dec 2026',
            evidence='Example-Code – Mapping System Jul 2026 – Dec 2026\nTech: Node.js',
        ),
        job('Example Portal', None, 'Nov 2025', evidence='Example Portal Nov 2025 – Dec 2025\nTech: PHP'),
    ]
    jobs = [
        job('Open Source Contribution', 'EXAMPLESOC', 'May 2026', 'July 2026'),
        job(
            'Example-Code',
            None,
            'Jul 2026',
            'Dec 2026',
            evidence='Example-Code – Mapping System Jul 2026 – Dec 2026',
        ),
        job('Example Portal', None, 'Nov 2025', 'Dec 2025', evidence='Example Portal Nov 2025 – Dec 2025'),
    ]
    assert [j['title'] for j in drop_projects_filed_as_jobs(jobs, projects)] == ['Open Source Contribution']
    # A job with an employer is a job even when a project shares its name.
    employed = [job('Example Portal', 'Example Ltd', '2024')]
    assert drop_projects_filed_as_jobs(employed, projects) == employed


SUMMARY = 'IT student who builds example tools and likes machine learning.'
BLOCK = (
    'Web Intern | Example Academy Aug 2025\nSKILLS\n● Python, Java\n\nEDUCATION\nB.E. in Example Technology'
)


def test_a_summary_holding_other_sections_is_refused():
    assert clean_summary(SUMMARY) == SUMMARY
    assert clean_summary(BLOCK) is None
    variants = [
        {'value': SUMMARY, 'evidence': 'RIYA EXAMPLE\nSUMMARY\n' + SUMMARY + '\nPROJECTS\nExample App'},
        {'value': BLOCK, 'evidence': BLOCK},
    ]
    assert clean_summary_variants(variants) == variants[:1]


def test_one_summary_left_is_the_summary_with_no_choice_to_make():
    source = 'RIYA EXAMPLE\nSUMMARY\n' + SUMMARY + '\n' + BLOCK
    response = {
        'summary': None,
        'warnings': [],
        '_scalar_variants': {
            'summary': [{'value': SUMMARY, 'evidence': SUMMARY}, {'value': BLOCK, 'evidence': BLOCK}]
        },
    }
    warnings = []
    data, _ = checked_facts(response, source, warnings)
    assert data['summary'] == SUMMARY
    assert not any('summary' in w for w in warnings)


def test_skill_lines_become_one_skill_each():
    lines = [
        'Technical Skills: • Programming Languages: Python ,Numpy,Java, JavaScript. • Frontend : Html , CSS.',
        'Tools: Docker, Git, Web Scraping (BeautifulSoup, Scrapy)',
        'C/C++',
        'Python (basics)',
        'Git',
    ]
    assert split_skills(lines) == [
        'Python',
        'Numpy',
        'Java',
        'JavaScript',
        'Html',
        'CSS',
        'Docker',
        'Git',
        'Web Scraping (BeautifulSoup, Scrapy)',
        'C/C++',
        'Python (basics)',
    ]


# --- contact details, graduation year, institution (1.3.3) -------------------------------

from agents_service.agents.cv.cleanup import (  # noqa: E402
    clean_location,
    clean_phone,
    email_from_document,
    tidy_scalars,
)


def test_email_from_the_document_without_ocr_junk_in_front():
    source = 'RIYA EXAMPLE\n& 5550100200 &% _riya.example5@example.com 9 Exampletown.State'
    assert email_from_document(source) == 'riya.example5@example.com'
    data, variants = tidy_scalars({'email': None, 'education': []}, {}, source)
    assert data['email'] == 'riya.example5@example.com'
    assert email_from_document('RIYA EXAMPLE\nNo contact line') is None


def test_icons_in_front_of_a_phone_or_a_place_are_dropped():
    assert clean_phone('& 5550100200') == '5550100200'
    assert clean_phone('+91 5550100200') == '+91 5550100200'
    assert clean_location('9 Exampletown.State') == 'Exampletown.State'
    assert clean_location('© Exampletown, State') == 'Exampletown, State'
    assert clean_location('9 Example Road, Exampletown') == '9 Example Road, Exampletown'


EDUCATION = [
    {'title': 'BE, Information Technology', 'organization': 'Example College', 'end_date': '2028'},
    {'title': 'HSC', 'organization': 'Example Junior College', 'end_date': '2024'},
]


def test_a_graduation_year_needs_a_degree_end_or_a_line_that_says_so():
    source = 'Intern Aug 2025 – Nov 2025\nBE, Information Technology June 2024 – 2028'
    assert (
        tidy_scalars({'graduation_year': 2028, 'education': EDUCATION}, {}, source)[0]['graduation_year']
        == 2028
    )
    assert (
        tidy_scalars({'graduation_year': 2025, 'education': EDUCATION}, {}, source)[0]['graduation_year']
        is None
    )
    cue = 'Expected graduation: 2027'
    assert tidy_scalars({'graduation_year': 2027, 'education': []}, {}, cue)[0]['graduation_year'] == 2027
    # HSC is not a degree: its year is not a graduation year.
    assert (
        tidy_scalars({'graduation_year': 2024, 'education': EDUCATION}, {}, source)[0]['graduation_year']
        is None
    )


def test_an_institution_must_be_a_school():
    data, variants = tidy_scalars(
        {'institution': None, 'education': EDUCATION},
        {
            'institution': [
                {'value': 'Example Society Pune Chapter', 'evidence': 'x'},
                {'value': 'Example College of Engineering, Exampletown', 'evidence': 'y'},
            ]
        },
        '',
    )
    assert data['institution'] == 'Example College of Engineering, Exampletown'
    assert 'institution' not in variants
    place = tidy_scalars({'institution': 'Exampletown, State, India', 'education': []}, {}, '')[0]
    assert place['institution'] is None
