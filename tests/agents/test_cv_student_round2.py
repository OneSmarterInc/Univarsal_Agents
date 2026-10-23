"""Fixes from the second laptop run on student CVs (version 1.2.7), with made-up people:
a model timeout skips one section only, OCR does not repeat a page's own text,
education lines with the degree and school on one line, certificate and award
lists read in full, and roles under "Internship Experience" or without dates."""

from unittest.mock import patch

from agents_service.agents.cv.local_reader import extract_local_resume
from agents_service.agents.cv.source_education import layout_education
from agents_service.agents.cv.source_lists import layout_items
from agents_service.agents.cv.source_records import layout_entries
from agents_service.ai.providers import AIError, Completion
from agents_service.documents.reading import _with_ocr_lines
from agents_service.worker.runner import all_files_lost_model
from tests.agents.kormic_adapter import CONFIG


def short(records):
    return [(r['title'], r['organization'], r['start_date'], r['end_date']) for r in records]


def titles(records):
    return [r['title'] for r in records]


# --- OCR on a page that already has text -------------------------------------------


def test_ocr_copy_of_the_page_text_is_left_out():
    text = 'PROJECTS\n• Built an AI-powered helper for the Example Club.\n• Java (working knowledge) • Python (Basics)'
    ocr = (
        '« Built an Al-powered helper for the Example Club.\n'
        '+ Java (workingknowledge) + Python (Basics)\n'
        'Certified Example Badge'  # text only inside a picture
    )
    assert _with_ocr_lines(text, ocr) == text + '\nCertified Example Badge'


def test_short_words_from_pictures_are_kept():
    # Skill logos: "Java" is not "JavaScript", "SQL" is not "MySQL".
    assert _with_ocr_lines('Skills: JavaScript, MySQL', 'C++\nGo\nJava\nSQL\n0\n2024-2028') == (
        'Skills: JavaScript, MySQL\nC++\nGo\nJava\nSQL\n2024-2028'
    )


def test_a_scanned_page_keeps_all_ocr_text():
    assert _with_ocr_lines('', 'RIYA EXAMPLE\nExample College') == '\nRIYA EXAMPLE\nExample College'


def test_nothing_new_from_ocr_leaves_the_text_unchanged():
    text = 'EDUCATION\nBachelor of Engineering — Example College'
    assert _with_ocr_lines(text, 'Bachelor of Engineering - Example College') == text


# --- a model timeout -------------------------------------------------------------------

STUDENT = """RIYA EXAMPLE
riya@example.com | 5550100200
EDUCATION
B.E (IT) 8.10 SGPA Example College of Engineering, Exampletown (Pursuing)
HSC 80.50 % Example Junior College,Exampletown 2024
SSC 90.10 % Example English Vidyalaya ,Exampletown. 2022
SKILLS
Python, Java"""


def _empty(kwargs):
    props = kwargs['schema']['properties']
    return Completion(
        data={k: ([] if v.get('type') == 'array' or 'anyOf' not in v else None) for k, v in props.items()}
    )


def _run(text, generate):
    config = dict(CONFIG, chunk_chars=3000, text_first=True, file_seconds=600, request_seconds=120)
    with (
        patch('agents_service.agents.cv.local_reader.extract_owner', return_value=[]),
        patch('agents_service.agents.cv.local_reader.provider') as factory,
    ):
        factory.return_value.__enter__.return_value.generate.side_effect = generate
        return extract_local_resume(text, config)


def test_one_timed_out_question_skips_that_section_and_reads_it_from_the_document():
    def generate(**kwargs):
        if 'education' in kwargs['schema']['properties']:
            raise AIError('timeout')
        return _empty(kwargs)

    result = _run(STUDENT, generate)
    assert titles(result['education']) == ['B.E (IT)', 'HSC', 'SSC']


def test_a_timed_out_section_with_no_plain_layout_is_noted():
    def generate(**kwargs):
        if 'summary' in kwargs['schema']['properties']:
            raise AIError('timeout')
        return _empty(kwargs)

    text = STUDENT.replace('EDUCATION', 'SUMMARY\nA student who likes building small tools.\nEDUCATION')
    result = _run(text, generate)
    assert any('summary details could not be read' in w for w in result['warnings'])
    assert titles(result['education']) == ['B.E (IT)', 'HSC', 'SSC']


def test_a_model_that_stalls_on_every_question_still_stops_the_file():
    def generate(**kwargs):
        raise AIError('timeout')

    try:
        _run(STUDENT, generate)
    except AIError as exc:
        assert exc.reason == 'timeout'
    else:
        raise AssertionError('expected a timeout')


def test_a_job_whose_files_all_timed_out_goes_back_to_the_line():
    files = [{'index': 0, 'status': 'failed', 'diagnostic': {'stage': 'provider', 'reason': 'timeout'}}]
    assert all_files_lost_model(files)


# --- education ------------------------------------------------------------------------


def test_degree_grade_and_school_on_one_line():
    assert short(layout_education(STUDENT)) == [
        ('B.E (IT)', 'Example College of Engineering', None, None),
        ('HSC', 'Example Junior College', None, '2024'),
        ('SSC', 'Example English Vidyalaya', None, '2022'),
    ]


def test_school_after_at_or_a_dash_and_a_name_that_wraps():
    text = """EDUCATION
Bachelor of Engineering in Information Technology at Example Sant College of
Engineering, Exampletown
SSC-(91.16%)Example Vidya Mandir, Exampleville
HSC — Example Shivaji Junior College 2023
SKILLS"""
    assert short(layout_education(text)) == [
        (
            'Bachelor of Engineering in Information Technology',
            'Example Sant College of Engineering',
            None,
            None,
        ),
        ('SSC', 'Example Vidya Mandir', None, None),
        ('HSC', 'Example Shivaji Junior College', None, '2023'),
    ]


def test_secondary_school_is_a_degree_name_and_a_dash_in_a_degree_is_kept():
    text = """EDUCATION
Bachelor of Engineering – Information Technology Sep 2024 – Present
Example College of Engineering, Exampletown | Currently in 3rd Year (2026–27)
Secondary School (Class X)
Higher Secondary Certificate (HSC) 03/2022
Example Jr. College, Exampletown"""
    assert short(layout_education(text)) == [
        (
            'Bachelor of Engineering – Information Technology',
            'Example College of Engineering',
            'Sep 2024',
            'Present',
        ),
        ('Secondary School (Class X)', None, None, None),
        ('Higher Secondary Certificate (HSC)', 'Example Jr. College', None, None),
    ]


def test_one_unclear_entry_no_longer_hides_the_clear_ones():
    text = 'EDUCATION\nBachelor of Arts\n2010 2012 2014\n\nHSC 80% Example Junior College 2008'
    assert titles(layout_education(text)) == ['HSC']
    assert layout_education('Education\nBachelor of Arts\n2010 2012 2014') is None


# --- certificate and award lists ----------------------------------------------------------


def test_every_certificate_in_a_list_is_read():
    text = """CERTIFICATION
¢ Introduction to Example Cloud Applications (EX101 - RHA)
Ver. 4.6
* Example Training: Getting Started with Linux Fundamentals
(EX104 - RHA) - Ver. 9.1
« Example Academy - Cloud Foundations
« Example Academy - Cloud Architecting (Apr 2026)
ACHIEVEMENTS
Secured 1st Position in a Startup Competition.
Participated in a Hackathon."""
    certificates = layout_items(text, 'certifications')
    assert titles(certificates) == [
        'Introduction to Example Cloud Applications (EX101 - RHA)',
        'Example Training: Getting Started with Linux Fundamentals',
        'Example Academy - Cloud Foundations',
        'Example Academy - Cloud Architecting',
    ]
    assert certificates[0]['description'] == 'Ver. 4.6'
    assert certificates[3]['end_date'] == 'Apr 2026'
    assert titles(layout_items(text, 'awards')) == [
        'Secured 1st Position in a Startup Competition',
        'Participated in a Hackathon',
    ]


def test_certificates_named_under_achievements_on_the_heading_line():
    text = (
        'Achievements • Completed EXAMPLE certification in Programming in Python. '
        '• Successfully completed certification in Java Programming.\nLangauges\n• English'
    )
    assert titles(layout_items(text, 'certifications')) == [
        'Completed EXAMPLE certification in Programming in Python',
        'Successfully completed certification in Java Programming',
    ]
    assert layout_items(text, 'awards') == []


def test_a_list_ends_at_its_next_heading_or_a_line_after_a_blank():
    text = (
        'CERTIFICATIONS\nProgramming in Java - EXAMPLE\nData Base Management System - EXAMPLE\n'
        'LANGUAGE\nEnglish Marathi Hindi'
    )
    assert titles(layout_items(text, 'certifications')) == [
        'Programming in Java - EXAMPLE',
        'Data Base Management System - EXAMPLE',
    ]
    signed = '● Example Academy Graduate – Cloud Foundations\n\nRIYA EXAMPLE'
    assert titles(layout_items('Certification\n' + signed, 'certifications')) == [
        'Example Academy Graduate – Cloud Foundations'
    ]


def test_narrow_column_text_and_skills_lines_are_left_to_the_model():
    narrow = 'AWARDS\nISTE State-Level Technical\nQuiz Competition (2024)\nSecured 3rd place in a\nstate quiz'
    assert layout_items(narrow, 'awards') == []
    skills = 'ACHIEVEMENTS\n• Frameworks : React, Node, Express\n• Won the Example Hackathon 2025'
    assert titles(layout_items(skills, 'awards')) == ['Won the Example Hackathon']


def test_a_date_in_brackets_leaves_a_clean_title():
    text = 'AWARDS\n• Example Student Ambassador (ESA 2026)'
    assert titles(layout_items(text, 'awards')) == ['Example Student Ambassador']


# --- roles ------------------------------------------------------------------------------------


def test_internship_heading_and_roles_without_dates():
    text = """INTERNSHIP EXPERIENCE
AI/ML Intern | Example Society — AI/ML Internship Programme June 2026 – July 2026
● Working on an example classifier.
PROFESSIONAL EXPERIENCE
Example Club— Technical Member
• Active technical member contributing to workshops and events
Volunteer — Management Team, EXAMPLEFEST 2026
• Contributed to a National level Hackathon and the National level
Project Expo
• Managed the exhibits.
Member, Example Developers Student Club
• Active member in technical sessions.
EDUCATION
Bachelor of Engineering"""
    assert short(layout_entries(text, 'work')) == [
        ('AI/ML Intern', 'Example Society', 'June 2026', 'July 2026'),
        ('Example Club— Technical Member', None, None, None),
        ('Volunteer', 'Management Team, EXAMPLEFEST', '2026', None),
        ('Member, Example Developers Student Club', None, None, None),
    ]


def test_a_heading_after_a_bullet_without_a_full_stop_still_ends_the_section():
    text = 'Projects\nChat App | 2023\n• Built a chat app using Socket.io\nCertifications\n• Example Cloud Practitioner'
    (project,) = layout_entries(text, 'projects')
    assert project['description'] == '• Built a chat app using Socket.io'
    work = 'EXPERIENCE\nIntern | Example Labs | 2025\n• Built example dashboards\nSkills\n• Python'
    (job,) = layout_entries(work, 'work')
    assert job['description'] == '• Built example dashboards'
