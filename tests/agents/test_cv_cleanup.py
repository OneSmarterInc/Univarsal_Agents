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


# --- skills, split names and emails, repeats across sections (1.3.4) ---------------------

from agents_service.agents.cv.cleanup import (  # noqa: E402
    add_document_skills,
    drop_bare_links,
    drop_cross_repeats,
    drop_study_years,
    drop_title_technologies,
    join_split_name,
    join_wrapped_titles,
    major_only,
)

SKILLS_CV = (
    'RIYA EXAMPLE\nSKILLS\n● Programming Languages: Python, C, Java (basics)\n'
    '● Generative AI: LangChain, OpenAI API\n• PowerPoint, Canva (Design & Presentation Tool\n'
    'Comfortable working with many teams across many example projects every week\nEDUCATION\nB.E. Example'
)


def test_every_labelled_skill_line_is_read():
    skills = add_document_skills(['Python', 'C', 'Java'], SKILLS_CV)
    assert skills == ['Python', 'C', 'Java', 'LangChain', 'OpenAI API', 'PowerPoint', 'Canva']


def test_a_name_and_an_email_over_two_lines_are_joined():
    source = 'RIYA EXAMPLE\nKUMARI\nEMAIL\nriya.example65@g\nmail.com'
    owners = [{'value': 'RIYA EXAMPLE', 'evidence': 'x'}, {'value': 'KUMARI', 'evidence': 'y'}]
    assert join_split_name(owners, source) == [
        {'value': 'RIYA EXAMPLE KUMARI', 'evidence': 'RIYA EXAMPLE\nKUMARI'}
    ]
    data, _ = tidy_scalars({'email': None, 'education': []}, {}, source)
    assert data['email'] == {'value': 'riya.example65@gmail.com', 'evidence': 'riya.example65@g\nmail.com'}


def test_an_item_in_two_sections_is_kept_once():
    data = {
        'awards': [job('Best Idea Award', 'Example Cell', 'Aug 2025', evidence='Best Idea Award – Contest 2025, Example Cell (Aug 2025) for Example')],
        'certifications': [
            job('Best Idea Award – Contest 2025', 'Example Cell', 'Aug 2025', evidence='Best Idea Award – Contest 2025, Example Cell (Aug 2025)'),
            job('Example Cloud Foundations', 'Example Academy', evidence='Example Cloud Foundations, Example Academy'),
        ],
        'volunteering': [job('Volunteer', 'EXAMPLEFEST', evidence='Volunteer — Team, EXAMPLEFEST 2026 • Ran the expo')],
        'work_experience_entries': [
            job('Volunteer', 'Team, EXAMPLEFEST', '2026', evidence='Volunteer — Team, EXAMPLEFEST 2026\n• Ran the expo'),
            job('Web Intern', 'Example Ltd', '2025', evidence='Web Intern | Example Ltd 2025'),
        ],
    }  # fmt: skip
    drop_cross_repeats(data)
    assert [c['title'] for c in data['certifications']] == ['Example Cloud Foundations']
    assert [w['title'] for w in data['work_experience_entries']] == ['Web Intern']


def test_wrapped_titles_study_years_and_title_technologies():
    award = job(
        'Played district cricket, showing dedication to',
        description='sports.',
        evidence='Played district cricket, showing dedication to\nsports.',
    )
    same_line = job(
        'Best Idea Award', description='for Example App', evidence='Best Idea Award (2025) for Example App'
    )
    assert join_wrapped_titles([same_line])[0]['title'] == 'Best Idea Award'
    assert join_wrapped_titles([award])[0]['title'] == 'Played district cricket, showing dedication to sports'
    cert = job('Example Training', description='Ver. 4.6')
    assert join_wrapped_titles([cert])[0]['description'] == 'Ver. 4.6'
    degree = job(
        'B.E. Example',
        'Example College',
        None,
        '2024',
        evidence='B.E. Example | Example College\n• In 3rd year (2024–25 SGPA: 7.8)',
    )
    assert drop_study_years([degree])[0]['end_date'] is None
    span = job('BE', None, 'June 2024', '2028', evidence='BE, Example; Average SGPA: 8.6 June 2024 – 2028')
    assert drop_study_years([span])[0]['end_date'] == '2028'
    titled = {
        **job('Cloud Architecting', 'Example Web Services'),
        'technologies': ['Cloud Architecting', 'Example Web Services', 'Docker'],
    }
    assert drop_title_technologies([titled])[0]['technologies'] == ['Docker']


def test_majors_links_and_a_college_town():
    assert major_only('B.E. Information Technology') == 'Information Technology'
    assert major_only('Bachelor of Engineering in Information Technology') == 'Information Technology'
    assert major_only('Mechanical Engineering') == 'Mechanical Engineering'
    data, variants = tidy_scalars(
        {'major': None, 'education': []},
        {'major': [{'value': 'B.E. Information Technology', 'evidence': 'long block'}, {'value': 'Information Technology', 'evidence': 'x'}]},
        '',
    )  # fmt: skip
    assert data['major'] == 'Information Technology' and 'major' not in variants
    assert drop_bare_links(['LinkedIn', 'GitHub: example-user']) == ['GitHub: example-user']
    source = 'RIYA EXAMPLE\n+91 5550100200\nEDUCATION\nExample College of Engineering Exampletown, India'
    assert tidy_scalars({'location': 'India', 'education': []}, {}, source)[0]['location'] is None
