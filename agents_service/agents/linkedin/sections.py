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
TOP_SKILLS = re.compile(r"((?:[^\W\d_][\w.\-]*(?:'|’)s\s+)?top skills)[\s:]*$")


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
