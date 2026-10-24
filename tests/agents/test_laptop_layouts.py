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


# --- second laptop round: 7 student CVs ----------------------------------------------

PROJECTS_CV = """RIYA EXAMPLE
riya@example.com | Exampletown
PROJECTS
Student Online Portal
Technologies: HTML, CSS, MySQL
● Built a web portal for student records.
● Used MySQL for the records.
Clinic Bridge
Hackathon Project | Technologies: MERN Stack
● Linked two medical code lists.
Voice Messenger | Personal Project
• Built a voice messaging tool that turns speech
into text messages.
• Used Python and gTTS.
CERTIFICATIONS & ACHIEVEMENTS
● German Language – A1
EXTRACURRICULARACTIVITIES
• Member of the coding club."""


def test_projects_without_dates_are_read_from_their_titles_and_bullets():
    projects = layout_entries(PROJECTS_CV, 'projects')
    assert [p['title'] for p in projects] == ['Student Online Portal', 'Clinic Bridge', 'Voice Messenger']
    assert 'Technologies: HTML, CSS, MySQL' in projects[0]['description']
    assert projects[2]['description'].endswith('Used Python and gTTS.')
    assert all('German' not in p['evidence'] for p in projects)


def test_a_bullet_line_with_a_year_is_never_a_new_entry():
    source = """PROJECTS
Mind Map App Aug 2025
• Built a planning app for students.
• Won the Best Idea Award at Example Fest 2025."""
    assert [p['title'] for p in layout_entries(source, 'projects')] == ['Mind Map App']


def test_a_title_that_wraps_onto_a_second_line_keeps_its_date():
    source = """PROJECTS
Skin Check — Image Classifier Example Chapter AI/ML
Internship, 2026
● Trained a classifier on 15,000
images from two public sets.
Room Finder
● Built a rental site."""
    projects = layout_entries(source, 'projects')
    assert [(p['title'], p['start_date']) for p in projects] == [
        ('Skin Check', '2026'),  # the title is the part before the dash, as for every entry
        ('Room Finder', None),
    ]


def test_headings_are_not_degrees():
    from agents_service.agents.cv.source_education import DEGREE, is_section_heading

    for heading in ('ABOUT ME', 'P R O F E S S I O N A L', 'doctors and patients'):
        assert not DEGREE.search(heading), heading
    for degree in ('BE', 'BE (Information Technology)', 'B.E. in IT', 'HSC', 'Doctor of Philosophy'):
        assert DEGREE.search(degree), degree
    assert is_section_heading('EXTRACURRICULARACTIVITIES') and is_section_heading('Leadership & Activities')
    assert not is_section_heading('Digital Lab Projects Example') and not is_section_heading(
        'Example College'
    )


def test_model_education_without_a_degree_or_school_links_and_languages_are_checked():
    source = 'B.E (IT) 8.56 SGPA Example College\nHSC 2024\nriya@example.com\nEnglish Hindi Python\ngithub.com/riya'
    junk = {'title': '(IT) 8', 'organization': None, 'start_date': '2024', 'end_date': None,
            'description': None, 'technologies': [], 'url': None, 'evidence': 'B.E (IT) 8.56 SGPA Example College\nHSC 2024'}  # fmt: skip
    kept = supported_resume(
        {'education': [junk], 'links': ['riya@example.com', 'github.com/riya'],
         'languages': ['English', 'Hindi', 'Python'], 'skills': ['Python'], 'warnings': []},
        source,
    )  # fmt: skip
    assert kept['education'] == []
    assert kept['links'] == ['github.com/riya']
    assert kept['languages'] == ['English', 'Hindi']


def test_pdf_text_repairs():
    from agents_service.documents.reading import PRIVATE_USE, SPACED_HEADING, letter_per_line, repair_mojibake

    assert repair_mojibake('Example State â€“ 444000') == 'Example State – 444000'
    assert PRIVATE_USE.sub('•', ' Python') == '• Python'
    assert SPACED_HEADING.sub(lambda m: m.group().replace(' ', ''), 'C O N T A C T\nE D U C AT I O N') == (
        'CONTACT\nEDUCATION'
    )
    assert letter_per_line('\n'.join('RIYAEXAMPLESTUDENTCV')) and not letter_per_line('Riya Example\n' * 30)
