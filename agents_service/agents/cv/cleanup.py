"""Tidy a CV's checked facts before they become records.

Every fact here has already passed the evidence check. These rules only remove
repeats and plain mistakes of the small model, or split a list it returned as
one line; every value that remains is still a copy of the document's text.
"""

import re

from agents_service.agents.cv.source_education import EDUCATION_HEADING, OTHER_HEADING, is_section_heading
from agents_service.documents.matching import canonical

ENTRY_SECTIONS = (
    'education',
    'work_experience_entries',
    'projects',
    'certifications',
    'awards',
    'publications',
    'volunteering',
)
# "Backend: Django, Node.js" / "Technical Skills: • Programming Languages: Python ,Numpy"
SKILL_LABEL = re.compile(r'^[^\W\d_][\w &/+().-]{0,40}:\s*')
SKILL_SEPARATORS = ',;|•●▪'
MAX_SKILL_CHARS = 40


def _key(text):
    return canonical(text or '').strip(' .,;:-–—')


def _entry_key(entry):
    return (
        _key(entry.get('title')),
        _key(entry.get('organization')),
        entry.get('start_date'),
        entry.get('end_date'),
    )


def _richness(entry):
    return len(entry.get('description') or '') + len(entry.get('evidence') or '')


def merge_repeats(entries):
    """One entry per title, organisation and dates; the one with more detail is kept.

    The model and the document reading can both find a job: once with its bullets,
    once without them.
    """
    kept = []
    for entry in entries:
        key = _entry_key(entry)
        twin = next((n for n, other in enumerate(kept) if _entry_key(other) == key), None)
        if twin is None:
            kept.append(entry)
        elif _richness(entry) > _richness(kept[twin]):
            kept[twin] = entry
    return kept


def _first_line(entry):
    return _key((entry.get('evidence') or '').split('\n', 1)[0])


def drop_projects_filed_as_jobs(jobs, projects):
    """A job that is the same entry as a project (same title, or the same first line of
    the document) is the project, filed twice. It stays a project."""
    project_titles = {_key(p.get('title')) for p in projects}
    project_lines = {_first_line(p) for p in projects}
    return [
        job
        for job in jobs
        if not (
            job.get('organization') is None
            and (_key(job.get('title')) in project_titles or _first_line(job) in project_lines)
        )
    ]


def _holds_headings(text):
    """True when the text runs over two or more section headings: a block of the CV,
    not a summary."""
    lines = [line.strip() for line in (text or '').split('\n') if line.strip()]
    headings = [
        line
        for line in lines
        if OTHER_HEADING.match(line) or EDUCATION_HEADING.fullmatch(line) or is_section_heading(line)
    ]
    return len(headings) >= 2


def clean_summary(value):
    """None when the 'summary' is a block of the CV holding other sections."""
    return None if isinstance(value, str) and _holds_headings(value) else value


def clean_summary_variants(entries):
    """The summary alternatives whose own text is a summary (the quote may be longer)."""
    return [entry for entry in entries if not _holds_headings(entry.get('value'))]


def _split_outside_brackets(text):
    """Parts at , ; | and bullets, but not inside brackets: "Scraping (BeautifulSoup, Scrapy)" stays whole."""
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in '([':
            depth += 1
        elif char in ')]':
            depth = max(0, depth - 1)
        elif char in SKILL_SEPARATORS and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts]


def split_skills(skills):
    """Skills one per item: "Backend: Django, Node.js" -> "Django", "Node.js".

    Each item is a part of the line, so it is still a copy of the document's text.
    """
    result, seen = [], set()
    for skill in skills:
        text = skill.strip()
        parts = [text]
        pieces = _split_outside_brackets(text)
        if SKILL_LABEL.match(text) or len(pieces) > 1:
            parts = []
            for piece in pieces:
                while SKILL_LABEL.match(piece):
                    piece = SKILL_LABEL.sub('', piece, count=1)
                piece = piece.strip(' .')
                if piece and len(piece) <= MAX_SKILL_CHARS and re.search(r'[^\W\d_]', piece):
                    parts.append(piece)
            if not parts:
                parts = [text]
        for part in parts:
            key = canonical(part)
            if key not in seen:
                seen.add(key)
                result.append(part)
    return result


def tidy(data):
    """Apply the rules above to checked CV facts in place; returns data."""
    for section in ENTRY_SECTIONS:
        if isinstance(data.get(section), list):
            data[section] = merge_repeats(data[section])
    if isinstance(data.get('work_experience_entries'), list) and isinstance(data.get('projects'), list):
        data['work_experience_entries'] = drop_projects_filed_as_jobs(
            data['work_experience_entries'], data['projects']
        )
    if 'summary' in data:
        data['summary'] = clean_summary(data['summary'])
    if isinstance(data.get('skills'), list):
        data['skills'] = split_skills(data['skills'])
    return data
