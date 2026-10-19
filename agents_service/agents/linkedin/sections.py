"""Keep extraction requests small without truncating the uploaded source."""

import re

from agents_service.documents.text import chunks  # noqa: F401 - used by the LinkedIn reader

SCALARS = ('name', 'headline', 'about', 'location')
COLLECTIONS = ('skills', 'experiences', 'education', 'projects', 'posts', 'certifications')
HEADINGS = {
    'about': ('about',),
    'summary': ('about',),
    'skills': ('skills',),
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
}


def heading_of(line):
    value = line.strip().casefold()
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
