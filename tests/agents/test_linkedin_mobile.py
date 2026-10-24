"""LinkedIn mobile-app screenshots (made-up profile): the profile card and the "top skills" page."""

from agents_service.agents.linkedin.agent import screenshot_text
from agents_service.agents.linkedin.schemas import ImageObservation
from agents_service.agents.linkedin.sections import heading_of, sections
from agents_service.agents.linkedin.validation import validate_data

CARD = """752 @QeH- ie ôSl
Ç ( Search tf
Riya Example she/Her
AI ML Intern at Example Labs Inc
Riya, you aren't verified
yet
Example College of Engineering
Springfield
Mumbai Metropolitan Region
499 connections
Open to work : Everyone on Linkedin V4
Show details"""

SKILLS = """754 OOS ie
Ç- ___ Riya's top skills
Programming language:
i Polytechnic Student | Computer Science
Government Polytechnic College
Problem Solving
Web Development
Agentic AI Development
| AI ML Intern at Example Labs Inc
Amazon Web Services (AWS)
Learn more about these skills
Discover jobs, people, learning content and
conversations about these skills"""

EMPTY = {'name': None, 'headline': None, 'location': None, 'about': None, 'experiences': [], 'projects': [],
         'posts': [], 'certifications': [], 'education': [], 'skills': []}  # fmt: skip


def check(data, text):
    return validate_data(
        ImageObservation.model_validate({**EMPTY, **data}).model_dump(), screenshot_text(text, True)
    )


def test_the_mobile_top_skills_title_is_a_skills_heading():
    for title in ("Ç- ___ Riya's top skills", 'Riya’s top skills', 'Top skills'):
        assert heading_of(title) == 'top skills'
    for not_title in ('Problem Solving', 'Develops top skills for teams', 'AI training top skills'):
        assert heading_of(not_title) != 'top skills'
    assert [fields for fields, _ in sections(screenshot_text(SKILLS, True))][-1] == ('skills',)


def test_skills_on_the_top_skills_page_are_kept():
    names = ['Problem Solving', 'Web Development', 'Agentic AI Development', 'Amazon Web Services (AWS)']
    out = check({'skills': [{'name': n, 'evidence': n} for n in names]}, SKILLS)
    assert [s['name'] for s in out['skills']] == names


def test_a_page_title_is_never_a_headline():
    out = check({'headline': {'value': 'top skills', 'evidence': "Riya's top skills"}}, SKILLS)
    assert out['headline'] is None


def test_the_school_on_the_profile_card_is_kept():
    out = check(
        {
            'education': [
                {
                    'institution': 'Example College of Engineering',
                    'evidence': 'Example College of Engineering',
                }
            ]
        },
        CARD,
    )
    assert [e['institution'] for e in out['education']] == ['Example College of Engineering']


def test_a_company_on_the_profile_card_is_not_a_school():
    out = check(
        {'education': [{'institution': 'Example Labs Inc', 'evidence': 'AI ML Intern at Example Labs Inc'}]},
        CARD,
    )
    assert out['education'] == []


def test_the_phone_status_bar_is_not_read_as_a_headline():
    from agents_service.agents.linkedin.local_reader import tasks
    from agents_service.agents.linkedin.validation import clean_text

    shot = "754 OOS ie\n\n€- ___ Riya's top skills\n\nProblem Solving\n\nWeb Development\n"
    text = clean_text(shot, screenshot=True)
    assert not text.startswith('754')
    assert [fields for fields, _ in tasks(text)] == [('skills',)]
    # A first line that is real content stays.
    assert clean_text('1200 connections\nRiya Example', screenshot=True).startswith('1200 connections')
    assert clean_text('Riya Example\n754 OOS ie', screenshot=True).endswith('754 OOS ie')
