"""Shared rules for searching a list (an exclusion list) for a person.

Every list agent decides "match" the same way, here: a name alone is never an
identification, because names collide and change.

    no_match        searched, nothing came back. Not a clearance.
    possible_match  a record came back on a name only; a person must check it.
    match           a record came back and a strong identifier also agreed.
"""

import re

SCREEN_OUTCOMES = ('no_match', 'possible_match', 'match')
# Identifiers that tie a record to one person. A name is not one of them.
IDENTIFYING = ('npi', 'date_of_birth', 'licence_number', 'registry_number', 'ssn_last4')


# Words that are not part of whose name it is: titles, suffixes, credentials, firm types.
NOT_NAMES = {
    'dr', 'mr', 'mrs', 'ms', 'miss', 'mx', 'prof', 'rev', 'sir', 'jr', 'sr', 'ii', 'iii', 'iv',
    'md', 'do', 'rn', 'np', 'pa', 'phd', 'dds', 'dmd', 'lpn', 'aprn', 'crna', 'esq', 'lcsw', 'lpc',
    'cna', 'mba', 'bsn', 'msn', 'dnp', 'pharmd', 'cpa', 'pt', 'ot', 'slp', 'dc', 'od', 'facs',
    'inc', 'llc', 'ltd', 'corp', 'co', 'company', 'pc', 'pllc', 'llp', 'lp', 'the',
}  # fmt: skip


def name_tokens(value):
    """The words of a name in order, lower case. "O'Brien" and "Abad-Santos" are one word each."""
    return [t for t in (re.sub(r"[^\w'’\-]", '', raw) for raw in str(value or '').casefold().split()) if t]


def name_words(value):
    """A name as comparable words: lower case, punctuation removed, single letters dropped.

    A word written with an apostrophe or hyphen also counts joined, so "O'Brien"
    matches "OBRIEN" and "Abad-Santos" matches "ABADSANTOS".
    """
    words = set()
    for token in name_tokens(value):
        parts = [part for part in re.split(r"['’\-_]", token) if part]
        words.update(part for part in parts if len(part) > 1)
        joined = ''.join(parts)
        if len(parts) > 1 and len(joined) > 1:
            words.add(joined)
    return words


def core_words(value):
    """name_words without titles, suffixes, credentials and firm types."""
    return name_words(value) - NOT_NAMES


def person_key(value):
    """The first and last name words, without titles and suffixes, for comparing people."""
    tokens = [t for t in name_tokens(value) if len(re.sub(r"['’\-_]", '', t)) > 1]
    tokens = [t for t in tokens if re.sub(r"['’\-_]", '', t) not in NOT_NAMES]
    if not tokens:
        return set()
    ends = {tokens[0], tokens[-1]}
    return {word for token in ends for word in name_words(token)}


def same_person_or_firm(listed, wanted):
    """True when a listed name could be the one searched for.

    A middle name, title or suffix on either side does not matter: "Dr. Jane
    Elizabeth Smith Jr." can be "Jane Marie Smith". Firms compare without their
    type: "ACME HEALTH SERVICES INC" can be "Acme Health Services LLC".
    """
    listed_core, wanted_core = core_words(listed), core_words(wanted)
    if not listed_core or not wanted_core:
        return False
    if listed_core <= name_words(wanted) or wanted_core <= name_words(listed):
        return True
    key = person_key(wanted)
    return bool(key) and len(key) >= 2 and key <= name_words(listed)


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
