"""Text matching used by every evidence check: is this value really in this quotation,
and is this quotation really in the document?
"""

import re
import unicodedata


def canonical(text):
    """Text for comparison: Unicode-normalised, plain dashes and quotes, single spaces, lower case."""
    text = unicodedata.normalize('NFKC', str(text or ''))
    text = text.translate(str.maketrans({'–': '-', '—': '-', '’': "'", '‘': "'", '\u00ad': ''}))
    return re.sub(r'\s+', ' ', text).strip().casefold()


def evidence_supported(evidence: str, source: str) -> bool:
    """True if the quotation appears in the source (long quotes may differ only in spacing)."""
    if not evidence or not source:
        return False
    quote = canonical(evidence)
    if quote in canonical(source):
        return True
    # Text layers and OCR can split or join words ("D eveloped", "NAMEB.E.").
    # A long quote still counts when only the spaces differ.
    compact = re.sub(r'\s+', '', quote)
    return len(compact) >= 12 and compact in re.sub(r'\s+', '', canonical(source))


def value_supported(value: str | None, evidence: str) -> bool:
    """True if the value appears in the quotation as whole words (or is empty)."""
    if not value:
        return True
    return bool(re.search(r'(?<!\w)' + re.escape(canonical(value)) + r'(?!\w)', canonical(evidence)))


def normalized(value):
    """Case-folded text with single spaces (keeps punctuation)."""
    return ' '.join(unicodedata.normalize('NFC', value or '').casefold().split())
