"""Read certificate and award lists without a model.

A small model often returns only the first item of a list. These sections are
usually a plain list under their own heading, one item per bullet or line, so
the items can be copied from the document. Every value is a copy of the text;
the evidence is the item's own lines.
"""

import re

from agents_service.agents.cv.schemas import Fact
from agents_service.agents.cv.source_education import (
    DEGREE,
    EDUCATION_HEADING,
    OTHER_HEADING,
    date_candidates,
    is_section_heading,
)

CERTIFICATION_HEADING = re.compile(
    r'^\W*(?:(?:courses?|trainings?)\s*(?:&|and|/)\s*)?(?:certifications?|certificates?|licen[cs]es?)'
    r'(?:\s*(?:&|and|/)\s*(?:courses?|trainings?|licen[cs]es?|achievements?|awards?))?\s*:?\s*',
    re.I,
)
AWARD_HEADING = re.compile(
    r'^\W*(?:awards?|achievements?|honou?rs?|accomplishments?)'
    r'(?:\s*(?:&|and|/)\s*(?:awards?|achievements?|honou?rs?|recognitions?|extra[- ]?curriculars?|'
    r'activities|certifications?))?\s*:?\s*',
    re.I,
)
HEADINGS = {'certifications': CERTIFICATION_HEADING, 'awards': AWARD_HEADING}
# Bullets, including the characters OCR reads them as ("¢", "«", "+", "e ").
BULLET = re.compile(r'^\s*(?:[•●▪◦○■□➢➤►▶✓✔\-–*·¢«+#©>]|e(?=\s+[A-Z]))\s*')
INLINE_BULLET = re.compile(r'\s*[•●▪➢➤►✓✔]\s*')
CERTIFICATE_WORDS = re.compile(r'certif|licen[cs]e|course\b|training', re.I)
# "Frameworks : React, Node, Express" is a skills line, not a certificate or an award.
LABELLED_LIST = re.compile(r'^[\w &/+.-]{2,30}\s*:\s*(?:[^,:]{1,25},\s*){2,}')
# Lines this short, without bullets, are a narrow column wrapped line by line; where
# one item ends is unclear, so the model reads them.
NARROW_COLUMN = 32
MAX_ITEMS = 30


def _lines(source):
    lines, offsets, position = source.split('\n'), [], 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    return lines, offsets


def _heading_match(line, kind):
    """The heading match when the line starts the wanted section, else None.

    A heading may carry its first items on the same line:
    "Achievements • Completed NPTEL certification … • Successfully completed …".
    """
    match = HEADINGS[kind].match(line)
    if not match:
        return None
    rest = line[match.end() :].strip()
    if rest and not INLINE_BULLET.match(line[match.end() :]):
        return None  # "Certificate of Appreciation, SSGMCE" is an item, not a heading
    return match


# Headings the shared section words miss ("LANGUAGE", "Languages Known").
LIST_END = re.compile(
    r'^\W*(?:languages?(?:\s+known)?|hobbies|interests|strengths|declaration|'
    r'personal\s+(?:details|information)|references?|activities)\s*:?\s*$',
    re.I,
)


def _ends_section(line):
    return bool(
        LIST_END.match(line)
        or OTHER_HEADING.match(line)
        or EDUCATION_HEADING.match(line)
        or is_section_heading(line)
        or any(HEADINGS[k].fullmatch(line.strip()) for k in HEADINGS)
    )


def _sections(lines, kind):
    """For each wanted section: the heading line index, the column after the heading, and its lines."""
    found, current = [], None
    for index, line in enumerate(lines):
        match = _heading_match(line, kind)
        if match:
            current = (index, match.end(), [])
            found.append(current)
            continue
        if current is None:
            continue
        body = BULLET.sub('', line, count=1).strip()
        if _ends_section(line) or (DEGREE.search(body) and DEGREE.search(body).start() == 0):
            # A degree line starts Education, even where a two-column page lost its heading.
            current = None
            continue
        current[2].append(index)
    return found


def _items(source, lines, offsets, heading_index, column, ids):
    """(start offset, end offset, first line text, more lines) for each item."""
    items = []
    # Items on the heading line itself, split at their bullets.
    rest = lines[heading_index][column:]
    start = offsets[heading_index] + column
    for part in re.finditer(r'[^•●▪➢➤►✓✔]+', rest):
        text = part.group().strip()
        if text:
            lead = len(part.group()) - len(part.group().lstrip())
            items.append([start + part.start() + lead, start + part.start() + lead + len(text), text, []])
    bulleted = any(BULLET.match(lines[i]) for i in ids)
    widths = sorted(len(lines[i].strip()) for i in ids if lines[i].strip())
    if not bulleted and widths and widths[len(widths) // 2] < NARROW_COLUMN:
        return items
    blank_before = False
    for i in ids:
        line = lines[i]
        text = line.strip()
        if not text:
            blank_before = True
            continue
        bullet = BULLET.match(line)
        if bulleted and not bullet and blank_before:
            break  # a line after the list and a blank line (a signature, a footer) is not an item
        blank_before = False
        body = line[bullet.end() :].strip() if bullet else text
        # Without bullets each line is an item, unless it plainly goes on from the line
        # above ("Ver. 9.1", "(RH104 - RHA)", a lower-case start).
        follows = (not bulleted and (body[:1].islower() or body.startswith(('(', 'Ver', '-')))) or (
            bulleted and not bullet
        )
        if follows and items:
            items[-1][1] = offsets[i] + len(line.rstrip())
            items[-1][3].append(body)
            continue
        begin = offsets[i] + (bullet.end() if bullet else len(line) - len(line.lstrip()))
        items.append([begin, offsets[i] + len(line.rstrip()), body, []])
    return items


def _title(text):
    """The item without a trailing date in brackets: "AWS … Services (Apr 2026)" -> "AWS … Services"."""
    dates = date_candidates(text)
    if not dates:
        return text.strip(' .')
    cut = text[: dates[0].start()].rstrip(' -–—:,(').strip()
    if cut.count('(') > cut.count(')'):
        cut = cut[: cut.rindex('(')].rstrip(' -–—:,')  # "Google Student Ambassador (GSA 2026)"
    return cut if re.search(r'[^\W\d_]{3}', cut) else text.strip(' .')


def layout_items(source, kind):
    """Certificate ('certifications') or award ('awards') records from a plain list.

    Returns [] when the chunk has no such section. Certificates listed under an
    Achievements heading are read as certificates, not as awards.
    """
    if kind not in HEADINGS:
        raise ValueError(kind)
    lines, offsets = _lines(source)
    sections = _sections(lines, kind)
    if kind == 'certifications':
        # "Achievements • Completed NPTEL certification in Programming in Python."
        sections += [s for s in _sections(lines, 'awards') if s not in sections]
    records = []
    for heading_index, column, ids in sections:
        under_awards = bool(AWARD_HEADING.match(lines[heading_index])) and not CERTIFICATION_HEADING.match(
            lines[heading_index]
        )
        for start, end, first, more in _items(source, lines, offsets, heading_index, column, ids):
            is_certificate = bool(CERTIFICATE_WORDS.search(first))
            if kind == 'certifications' and under_awards and not is_certificate:
                continue
            if kind == 'awards' and is_certificate:
                continue
            title = _title(first)
            if len(re.findall(r'[^\W\d_]', title)) < 3 or LABELLED_LIST.match(title):
                continue
            dates = date_candidates(source[start:end])
            records.append(
                Fact(
                    title=title,
                    end_date=dates[-1].group() if dates else None,
                    description='\n'.join(more) or None,
                    evidence=source[start:end].strip(),
                ).model_dump()
            )
            if len(records) >= MAX_ITEMS:
                return records
    return records
