"""Layouts from the first laptop run, with made-up people: a student CV with one
"Work Experience / Projects" section and a mobile LinkedIn profile."""

from agents_service.agents.cv.evidence import supported_resume
from agents_service.agents.cv.source_education import date_candidates, layout_education
from agents_service.agents.cv.source_records import layout_entries
from agents_service.agents.linkedin.sections import sections, top_skills
from agents_service.agents.linkedin.validation import validate_data

CV = """RIYA EXAMPLE
Exampletown, Example State • 5550100200 • riya@example.com
SUMMARY
Curious Computer Science student interested in web development. Skilled in HTML and Python. Familiar with NumPy Actively
joins hackathons to learn by building.
WORK EXPERIENCE / PROJECTS
Team Member – Example Hackathon Feb 2025
Built a learning website for school students.
Used HTML and CSS.
Dec 2024
Voice Helper (Mini Project)
Built a voice assistant that answers questions.
Written in JavaScript.
Clinic Booking Website Jan 2026
Built a booking site for a small clinic.
EDUCATION
Bachelor of Engineering (B.E.) – Computer Science 2024 - 2028
Example College of Engineering, Exampletown
Current CGPA: 8.7
Higher Secondary Education (12th Grade) 2022 - 2024
Example State Board
Percentage: 80.00%
Secondary Education (10th Grade) Mar 2022
Example State Board
Percentage: 90.00%
ADDITIONAL INFORMATION
Technical Skills: HTML, CSS, Python."""


def short(records):
    return [(r['title'], r['organization'], r['start_date'], r['end_date']) for r in records]


def test_current_cgpa_is_not_a_date_but_present_still_is():
    assert [m.group() for m in date_candidates('Current CGPA: 8.7')] == []
    assert [m.group() for m in date_candidates('2024 - Current')] == ['2024', 'Current']
    assert [m.group() for m in date_candidates('Aug 2018 - Present')] == ['Aug 2018', 'Present']


def test_one_section_for_jobs_and_projects_with_a_date_above_a_title():
    assert short(layout_entries(CV, 'work')) == [('Team Member', 'Example Hackathon', 'Feb 2025', None)]
    projects = layout_entries(CV, 'projects')
    assert short(projects) == [
        ('Voice Helper (Mini Project)', None, 'Dec 2024', None),
        ('Clinic Booking Website', None, 'Jan 2026', None),
    ]
    assert projects[0]['description'].endswith('Written in JavaScript.')
    assert 'Clinic' not in projects[0]['evidence']


def test_school_board_entries_and_dates_off_the_title():
    assert short(layout_education(CV)) == [
        (
            'Bachelor of Engineering (B.E.) – Computer Science',
            'Example College of Engineering',
            '2024',
            '2028',
        ),
        ('Higher Secondary Education (12th Grade)', 'Example State Board', '2022', '2024'),
        ('Secondary Education (10th Grade)', 'Example State Board', None, 'Mar 2022'),
    ]


def test_a_summary_with_the_models_own_punctuation_keeps_the_cvs_wording():
    model = (
        'Curious Computer Science student interested in web development. Skilled in HTML and Python. '
        'Familiar with NumPy. Actively joins hackathons to learn by building.'
    )
    kept = supported_resume({'summary': model, 'warnings': []}, CV)
    assert kept['summary'].startswith('Curious Computer Science') and 'NumPy Actively' in kept['summary']
    made_up = supported_resume({'summary': model.replace('hackathons', 'contests'), 'warnings': []}, CV)
    assert made_up['summary'] is None


def test_a_degree_under_other_information_is_left_to_education():
    entry = {'title': 'Bachelor of Engineering (B.E.)', 'organization': None, 'start_date': None,
             'end_date': None, 'description': None, 'technologies': [], 'url': None,
             'evidence': 'Bachelor of Engineering (B.E.) – Computer Science 2024 - 2028'}  # fmt: skip
    assert supported_resume({'other_information': [entry], 'warnings': []}, CV)['other_information'] == []


CARD = """Riya Example she/Her
Intern at Example Labs

Example College of Engineering
Exampletown
Example Metropolitan Region

499 connections"""

SKILLS_PAGE = """€- ___ Riya's top skills

Programming language:

i Student | Computer Science
Example Polytechnic College

r Example Polytechnic Town

Problem Solving

Web Development

| Intern at Example Labs

Amazon Web Services (AWS)

Learn more about these skills"""


def test_the_mobile_top_skills_page_is_read_line_by_line():
    block = next(block for fields, block in sections(SKILLS_PAGE) if fields == ('skills',))
    assert [s['name'] for s in top_skills(block)] == [
        'Programming language',
        'Problem Solving',
        'Web Development',
        'Amazon Web Services (AWS)',
    ]


def test_card_school_with_a_made_up_degree_keeps_only_the_school():
    data = {'education': [{'institution': 'Example College of Engineering', 'degree': 'M.B.A.',
                           'field_of_study': 'Engineering', 'start_date': '2020', 'end_date': '2022',
                           'evidence': 'Example College of Engineering Exampletown Example Metropolitan Region'}]}  # fmt: skip
    out = validate_data(data, CARD)
    school = out['education'][0]
    assert school['institution'] == 'Example College of Engineering'
    assert school['degree'] is school['field_of_study'] is school['start_date'] is None


def test_a_skill_quoted_from_the_wrong_line_is_quoted_from_its_own():
    data = {'skills': [{'name': 'Web Development', 'evidence': '| Intern at Example Labs'}]}
    out = validate_data(data, SKILLS_PAGE)
    assert out['skills'] == [{'name': 'Web Development', 'evidence': 'Web Development'}]


def test_a_location_is_quoted_from_its_own_line():
    from agents_service.agents.linkedin.local_reader import _scalar_evidence

    assert _scalar_evidence('location', 'Example Metropolitan Region', CARD) == 'Example Metropolitan Region'
    location = {
        'location': {'value': 'Example Metropolitan Region', 'evidence': 'Example Metropolitan Region'}
    }
    out = validate_data(location, CARD)
    assert out['location']['value'] == 'Example Metropolitan Region'
