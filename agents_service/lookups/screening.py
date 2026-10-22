"""Shared rules for searching a list (an exclusion list) for a person.

Every list agent decides "match" the same way, here: a name alone is never an
identification, because names collide and change.

    no_match        searched, nothing came back. Not a clearance.
    possible_match  a record came back on a name only; a person must check it.
    match           a record came back and a strong identifier also agreed.
"""

SCREEN_OUTCOMES = ('no_match', 'possible_match', 'match')
# Identifiers that tie a record to one person. A name is not one of them.
IDENTIFYING = ('npi', 'date_of_birth', 'licence_number', 'registry_number', 'ssn_last4')


def name_words(value):
    """A name as comparable words: lower case, punctuation removed, single letters dropped."""
    text = ''.join(ch if ch.isalnum() or ch.isspace() else ' ' for ch in str(value or '').lower())
    return {word for word in text.split() if len(word) > 1}


def screen_outcome(hit, matched_on):
    """The only way a list search becomes a conclusion."""
    if not matched_on:
        raise ValueError('A screen must name the identifiers it searched on.')
    if not hit:
        return 'no_match'
    return 'match' if any(i in IDENTIFYING for i in matched_on) else 'possible_match'


def names(value):
    """One name or a list of names, as a clean list."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v or '').strip()]
    return []
