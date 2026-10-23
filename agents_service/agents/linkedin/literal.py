"""Recover explicit, common profile layouts without model inference.

Every value is a slice of the source. Unrecognized layouts remain the model's job.
"""

import re

from agents_service.agents.linkedin.schemas import ImageObservation
from agents_service.agents.linkedin.sections import heading_of, sections

DEGREE = re.compile(
    r'\b(?:bachelor|master|diploma|bsc|msc|phd|b\.tech|m\.tech|associate degree|doctor of)\b', re.I
)
DATE = re.compile(
    r'(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+)?(?:19|20)\d{2}|\bPresent\b',
    re.I,
)
ROLE = re.compile(r'\b(?:intern|engineer|developer|manager|consultant|designer|analyst|director)\b', re.I)


PRONOUNS = r'\b(?:He/Him|She/Her|They/Them)\b'


def _summary_skills(line, result):
    # LinkedIn's visible skills summary names are explicit evidence, not
    # an invitation to invent the additional hidden '+N' skills.
    summary = re.match(r'^(.*?)\s+and\s+\+\d+\s+skills\b', line, re.I)
    if not summary:
        return
    for value in summary[1].split(','):
        value = re.sub(r'^[^\w]+', '', value).strip()
        if value:
            result['skills'].append({'name': value, 'evidence': line})


def _pronoun_name(line, result):
    """The name on the line that shows the person's pronouns."""
    if not re.search(PRONOUNS, line, re.I):
        return
    value = re.split(PRONOUNS, line, flags=re.I)[0]
    value = re.sub(r'^[^\w]+|[\s@©®9]+$', '', value).strip()
    if 2 <= len(value.split()) <= 6 and '|' not in value:
        result['name'] = {'value': value, 'evidence': line}


def _contact_location(lines, index, line, result):
    """The location shown beside, or just above, 'Contact info'."""
    if 'contact info' not in line.casefold():
        return
    value = re.split(r'\bContact info\b', line, flags=re.I)[0].strip(' -·•')
    evidence = line
    if not value:
        previous = next(
            (lines[j] for j in range(index - 1, max(-1, index - 4), -1) if lines[j].strip()),
            '',
        )
        if ',' in previous and '|' not in previous:
            value, evidence = previous.strip(), previous
    if value and ',' in value:
        result['location'] = {'value': value, 'evidence': evidence}


def _headline(lines, index, line, result):
    """A 'Role at Company | Skill | Skill' headline, with its job and skills."""
    if not (ROLE.search(line) and re.search(r'@|\bat\b', line, re.I) and '|' in line):
        return
    block = [line]
    for extra in lines[index + 1 : index + 7]:
        if extra.strip() and '|' not in extra:
            # "… | Deep" / "Learning, Generative AI": the last part was cut at the line end.
            last = block[-1].rsplit('|', 1)[-1].split()
            if (
                block[-1].strip()
                and len(last) <= 2
                and not re.search(r'contact info|connections|followers', extra, re.I)
            ):
                block.append(extra)
            break
        block.append(extra)
    evidence = '\n'.join(block).rstrip()
    headline = re.split(r'Enhance profile|Add section', evidence, flags=re.I)[0].strip()
    headline = re.sub(r'\s*\n\s*', ' ', headline)
    result['headline'] = {'value': headline, 'evidence': evidence}
    # "Engineer | Python at Acme" has no employer in its first part.
    parts = re.split(r'\s*(?:@|\bat\b)\s*', line.split('|', 1)[0], maxsplit=1, flags=re.I)
    if len(parts) == 2 and parts[1].strip():
        title = re.sub(r'^[^\w]+', '', parts[0]).strip()
        result['experiences'].append({'title': title, 'company': parts[1].strip(), 'evidence': evidence})
    for name in re.split(r'\|', headline)[1:]:
        name = re.sub(r'\s+', ' ', name).strip()
        if name and not re.search(r'\.{2,}|…', name):
            result['skills'].append({'name': name, 'evidence': evidence})


def _skills_section(block, result):
    for line in block.splitlines()[1:]:
        if re.search(r'endorse|show all|assessment|\d+ skills', line, re.I):
            continue
        for value in re.split(r'[,|•]', line):
            value = value.strip()
            if value and len(value) <= 60:
                result['skills'].append({'name': value, 'evidence': block})


def _education_entry(rows, index, line):
    """One education entry anchored on a degree line, or None."""
    if (
        not DEGREE.search(line)
        or len(line) > 150
        or re.search(r'\b(?:currently|pursuing|completed|my| at | from )\b', line, re.I)
    ):
        return None
    previous = next((j for j in range(index - 1, -1, -1) if rows[j].strip()), None)
    if previous is None or heading_of(rows[previous]) == 'education':
        return None
    institution = re.sub(r'^[^\w]+', '', rows[previous]).strip()
    if DATE.search(institution) or DEGREE.search(institution):
        return None
    end = index + 1
    while end < len(rows) and (not rows[end].strip() or DATE.search(rows[end])):
        end += 1
    dates = DATE.findall('\n'.join(rows[index + 1 : end]))
    degree, sep, field = line.partition(',')
    return {
        'institution': institution,
        'degree': degree.strip(),
        'field_of_study': field.strip() if sep else None,
        'start_date': dates[0] if dates else None,
        'end_date': dates[1] if len(dates) >= 2 else None,
        'evidence': '\n'.join(rows[previous:end]),
    }


def _education_section(block, result):
    # Each degree line anchors its own entry, so dates cannot leak from a
    # neighboring school. Narrative sentences are not degree headings.
    rows = block.splitlines()
    for index, line in enumerate(rows):
        entry = _education_entry(rows, index, line)
        if entry:
            result['education'].append(entry)


def literal_observation(source):
    """Profile facts read directly from common LinkedIn layouts, without a model."""
    result = ImageObservation.empty().model_dump()
    lines = source.splitlines()
    for index, line in enumerate(lines):
        _summary_skills(line, result)
        _pronoun_name(line, result)
        _contact_location(lines, index, line, result)
        _headline(lines, index, line, result)
    for fields, block in sections(source):
        heading = heading_of(block.splitlines()[0])
        if heading in {'about', 'summary'}:
            value = block.split('\n', 1)[-1].strip()
            if value:
                result['about'] = {'value': value, 'evidence': block}
        if fields == ('skills',):
            _skills_section(block, result)
        if heading == 'education':
            _education_section(block, result)
    return ImageObservation.model_validate(result).model_dump()
