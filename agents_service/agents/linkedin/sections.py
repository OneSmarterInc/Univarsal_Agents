"""Keep extraction requests small without truncating the uploaded source."""

import re

from agents_service.documents.text import chunks  # noqa: F401 - used by the LinkedIn reader

SCALARS = ('name', 'headline', 'about', 'location')
COLLECTIONS = ('skills', 'experiences', 'education', 'projects', 'posts', 'certifications')
HEADINGS = {
    'about': ('about',),
    'summary': ('about',),
    'skills': ('skills',),
    'top skills': ('skills',),  # the mobile app: "<Name>'s top skills"
    'technical skills': ('skills',),
    'experience': ('experiences', 'skills'),
    'work experience': ('experiences', 'skills'),
    'education': ('education', 'skills'),
    'projects': ('projects', 'skills'),
    'posts': ('posts',),
    'activity': ('posts',),
    'certifications': ('certifications',),
    'licenses & certifications': ('certifications',),
    'licenses and certifications': ('certifications',),
}
STOP_HEADINGS = {
    'connected apps',
    'people also viewed',
    'who your viewers also viewed',
    'profile language',
    'public profile & url',
    'promoted',
    'people you may know',
    'you might like',
    'learn more about these skills',
}
# The mobile app titles its skills page "<Name>'s top skills"; OCR adds icon noise in front.
TOP_SKILLS = re.compile(
    r"((?:(?:[^\W\d_][\w.\-]*\s+){0,3}[^\W\d_][\w.\-]*(?:'|’)s\s+)?top skills)[\s:>›»)\]]*$"
)


def _top_skills_heading(value):
    """True for "top skills" or "<name>'s top skills", after icon noise with no real words."""
    found = TOP_SKILLS.search(value)
    return bool(found) and not re.search(r'[^\W\d_]{2,}', value[: found.start(1)])


def heading_of(line):
    value = line.strip().casefold()
    if _top_skills_heading(value):
        return 'top skills'
    for heading in sorted((*HEADINGS, *STOP_HEADINGS), key=len, reverse=True):
        if re.fullmatch(re.escape(heading) + r'[\s:+@©®0-9()\-oo]*', value):
            return heading
    return re.sub(r'[^a-z& ]', '', value).strip()


def sections(source):
    fields, lines = SCALARS + COLLECTIONS, []
    for line in source.splitlines():
        heading = heading_of(line)
        if heading in HEADINGS or heading in STOP_HEADINGS:
            if lines and fields:
                yield fields, '\n'.join(lines)
            fields = HEADINGS.get(heading, ())
            lines = [line]
        else:
            lines.append(line)
    if lines and fields:
        yield fields, '\n'.join(lines)


# On the mobile "top skills" page each skill is a line of its own after a blank line;
# under it LinkedIn shows where the skill was used ("Student | Computer Science",
# "Example College"), often with an icon OCR reads as a stray letter ("r Example ...").
ICON_PREFIX = re.compile(r'^(?:[^\w\s(]|[a-z])\s+\S')
PLACE_WORDS = re.compile(
    r'\b(?:college|university|school|polytechnic|institute|academy|inc|ltd|llc|corp|company|'
    r'intern|student|engineer|developer|manager|analyst|consultant)\b',
    re.I,
)


def top_skills(block):
    """[{'name', 'evidence'}] read from a mobile "top skills" page, each from its own line.
    [] for any other block."""
    lines = block.splitlines()
    if not lines or heading_of(lines[0]) != 'top skills':
        return []
    found = []
    for index, line in enumerate(lines[1:], start=1):
        text = line.strip()
        previous = lines[index - 1].strip()
        if (
            not text
            or previous
            or '|' in text
            or ICON_PREFIX.match(text)
            or PLACE_WORDS.search(text)
            or len(text) > 60
            or not re.search(r'[^\W\d_]{2,}', text)
        ):
            continue
        name = text.rstrip(':').strip()
        if name:
            found.append({'name': name, 'evidence': line})
    return found
