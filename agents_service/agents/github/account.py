"""Reading a GitHub account: pure functions on GitHub's JSON, tested without the network.

Ported from kormic-agents (github.py). Counting repositories and languages is
arithmetic, so no model is used.
"""

import re

from agents_service.lookups.values import clean

# GitHub's own rule for usernames: letters, digits and single hyphens, not at either end.
USERNAME = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$')
# Paths on github.com that are pages, never a person.
NOT_USERS = {
    'about', 'apps', 'blog', 'collections', 'contact', 'customer-stories', 'enterprise', 'explore',
    'features', 'issues', 'join', 'login', 'marketplace', 'new', 'notifications', 'orgs', 'organizations',
    'pricing', 'pulls', 'search', 'security', 'settings', 'site', 'sponsors', 'team', 'topics', 'trending',
}  # fmt: skip
# Repositories shown one by one; the rest are only counted.
SHOWN_REPOS = 25


def parse_username(value):
    """A username from anything a person might paste: "octo-cat", "@octo-cat",
    "github.com/octo-cat", "https://www.github.com/octo-cat/", a repository link.
    None when it is not a GitHub username."""
    text = (clean(value) or '').strip()
    if not text:
        return None
    text = re.sub(r'^(?:https?://)?(?:www\.)?', '', text, flags=re.I)
    if re.match(r'^github\.com(?:/|$)', text, re.I):
        text = text[len('github.com') :].lstrip('/')
    elif '/' in text or '.' in text.split('/')[0]:
        return None  # another site, or a path that is not on github.com
    text = text.lstrip('@')
    handle = re.split(r'[/?#]', text, maxsplit=1)[0].strip()
    if not USERNAME.match(handle) or handle.lower() in NOT_USERS:
        return None
    return handle


def year(stamp):
    text = clean(stamp) or ''
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else None


def day(stamp):
    text = clean(stamp) or ''
    return text[:10] if re.match(r'^\d{4}-\d{2}-\d{2}', text) else None


def language_share(repos):
    """[(language, whole percent)] by repository count, largest first.

    Counted by repository, not by bytes: bytes are dominated by whichever
    repository holds a vendored library or a dataset.
    """
    counts = {}
    for repo in repos:
        language = clean(repo.get('language'))
        if language:
            counts[language] = counts.get(language, 0) + 1
    total = sum(counts.values())
    if not total:
        return []
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(name, round(count * 100 / total)) for name, count in ranked]


def last_push(repos):
    stamps = sorted(d for d in (day(r.get('pushed_at')) for r in repos) if d)
    return stamps[-1] if stamps else None


def repo_summary(repo):
    return {
        'name': clean(repo.get('name')),
        'url': clean(repo.get('html_url')),
        'language': clean(repo.get('language')),
        'stars': int(repo.get('stargazers_count') or 0),
        'forks': int(repo.get('forks_count') or 0),
        'pushed_on': day(repo.get('pushed_at')),
        'created_on': day(repo.get('created_at')),
        'description': (clean(repo.get('description')) or '')[:300] or None,
        'topics': [t for t in (repo.get('topics') or []) if isinstance(t, str)][:10],
        'archived': bool(repo.get('archived')),
    }


def shown_repos(repos):
    """Original repositories, most recently pushed first, at most SHOWN_REPOS."""
    original = [r for r in repos if isinstance(r, dict) and not r.get('fork') and clean(r.get('name'))]
    original.sort(key=lambda r: str(r.get('pushed_at') or ''), reverse=True)
    return [repo_summary(r) for r in original[:SHOWN_REPOS]]


def activity(profile, repos):
    """Counts over the repositories read (forks are counted, never credited as own work)."""
    original = [r for r in repos if not r.get('fork')]
    return {
        'repos_read': len(repos),
        'public_repos': int(profile.get('public_repos') or 0),
        'original_repos': len(original),
        'forked_repos': len(repos) - len(original),
        'stars_on_original': sum(int(r.get('stargazers_count') or 0) for r in original),
        'languages': [
            {'language': name, 'percent': share} for name, share in language_share(original or repos)
        ],
        'last_push_on': last_push(repos),
    }


def gist_holds(gist, challenge):
    """True when a gist's description, a file name or a file's text holds the challenge."""
    if challenge in str(gist.get('description') or ''):
        return True
    for name, meta in (gist.get('files') or {}).items():
        if challenge in str(name):
            return True
        if isinstance(meta, dict) and challenge in str(meta.get('content') or ''):
            return True
    return False
