"""LinkedIn screenshots of a person's own profile (1.3.6): buttons and analytics are not
facts, every job is read from the layout, grouped roles keep their employer."""

from unittest.mock import patch

from PIL import Image

from agents_service.agents.linkedin.literal import literal_observation
from agents_service.agents.linkedin.source_jobs import layout_jobs, plain_jobs
from agents_service.agents.linkedin.validation import clean_text
from agents_service.documents import reading

EXPERIENCE = """Experience + 2

Software Developer
Example Labs, Inc. - Internship
Aug 2026 - Present - 3 mos
Ohio, United States - Remote

Chairperson of Example Society

Example College of Engineering, Exampletown «Full-time
Jul 2026 - Present - 4 mos

Exampletown, State, India

Example Fablab

1 yr 8 mos

On-site

Mentor

Full-time

Jan 2026 - Present - 10 mos

Exampletown, State, India

* Mentored students in Java and Figma.

+ Developed lesson plans for the lab.... more

Member

Mar 2025 - Jan 2026 - 11 mos

Exampletwn

+ Learned many tools at the lab.

Cascading Style Sheets (CSS), Computer-Aided Design (CAD) and +7 skills

Member
EXSOC» Full-time

‘Aug 2025 - Jul 2026 -1 yr
Exampletown, State, India - On-site

* Took part in technical events."""


def test_every_job_and_grouped_role_is_read_from_the_layout():
    jobs = layout_jobs(EXPERIENCE)
    assert [(j['title'], j['company'], j['start_date'], j['end_date']) for j in jobs] == [
        ('Software Developer', 'Example Labs, Inc.', 'Aug 2026', 'Present'),
        (
            'Chairperson of Example Society',
            'Example College of Engineering, Exampletown',
            'Jul 2026',
            'Present',
        ),
        ('Mentor', 'Example Fablab', 'Jan 2026', 'Present'),
        ('Member', 'Example Fablab', 'Mar 2025', 'Jan 2026'),
        ('Member', 'EXSOC', 'Aug 2025', 'Jul 2026'),
    ]
    assert (
        jobs[2]['description'].startswith('* Mentored students') and 'lesson plans' in jobs[2]['description']
    )
    assert jobs[0]['description'] is None


def test_the_models_wrong_jobs_are_dropped_or_cleaned():
    jobs = [
        {'title': 'Mentor', 'company': 'Full-time', 'start_date': 'Jan 2026', 'end_date': 'Present'},
        {'title': '+ Developed lesson plans for the lab.... more', 'company': 'Member'},
        {'title': 'Chairperson', 'company': 'Example College «Full-time'},
    ]
    assert plain_jobs(jobs) == [{'title': 'Chairperson', 'company': 'Example College'}]


def test_buttons_and_analytics_of_ones_own_profile_are_not_text():
    text = (
        'Riya Example © She/Her\nAdd custom button )\nAnalytics\n@ Private to you\n'
        '206 profile views al, 49 post impressions\nAdd connected apps\nShow all >\nCreateapost)\n'
        'Exampletown, State, India - Contact info'
    )
    assert (
        clean_text(text, screenshot=True)
        == 'Riya Example © She/Her\nExampletown, State, India - Contact info'
    )


def test_a_headline_cut_at_the_line_end_is_read_whole():
    source = (
        'Riya Example © She/Her\n\nB.E. IT 28 | Developer Intern @ Example Labs, Inc. | Deep\n'
        'Learning, Generative AI & Web Development\nExampletown, State, India - Contact info'
    )
    found = literal_observation(source)
    assert found['headline']['value'] == (
        'B.E. IT 28 | Developer Intern @ Example Labs, Inc. | Deep Learning, Generative AI & Web Development'
    )
    assert found['location']['value'] == 'Exampletown, State, India'


def test_a_small_screenshot_is_enlarged_before_ocr():
    sizes = []

    def read(image, **_kwargs):
        sizes.append(image.size)
        return 'Experience\nSoftware Developer at Example Labs with enough text'

    with patch('pytesseract.image_to_string', side_effect=read), patch('pytesseract.image_to_data'):
        reading.ocr(Image.new('RGB', (800, 600), 'white'))
        reading.ocr(Image.new('RGB', (2400, 1200), 'white'))
    assert sizes == [(1600, 1200), (2400, 1200)]
