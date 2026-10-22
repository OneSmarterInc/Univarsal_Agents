"""Text helpers shared by every agent."""

import re


def tidy_text(text, screenshot=False):
    """Plain whitespace clean-up that keeps every line of the document."""
    text = text.replace('\x00', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return '\n'.join(line.strip() for line in text.splitlines()).strip()


def chunks(source, limit=2200, overlap=350):
    """Cover every character, preferring line boundaries, with overlap."""
    start = 0
    while start < len(source):
        end = min(len(source), start + limit)
        if end < len(source):
            boundary = source.rfind('\n', start + limit // 2, end)
            if boundary > start:
                end = boundary
        yield source[start:end]
        if end == len(source):
            break
        # The overlap starts at the start of a line: a piece that begins inside a word
        # ("...ant Gajanan" from "Shri Sant Gajanan") is read as a new entry.
        back = end - overlap
        line_start = source.find('\n', back, end) + 1
        start = max(start + 1, line_start if 0 < line_start < end else back)
