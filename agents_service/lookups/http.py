"""One way to call an outside source: a time limit, a size limit, and errors
that never carry the address's query (where API keys live) or the answer.

    body = get_json('https://api.example/v1/search', params={'q': name, 'api_key': key},
                    seconds=15, source='Example')

The address and params are never logged together: logs show only the source,
the address without its query, and the status.

Raises SourceError with one of these kinds:

    unavailable    no answer, a timeout or a server error (5xx): worth one retry
    rejected       the source refused the request (4xx other than below)
    unauthorized   401 or 403: the key is missing, wrong or not allowed
    rate_limited   429: the source's own limit was reached
    bad_response   not JSON, or larger than the limit
"""

import json
import logging
import time
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)
# httpx logs every request with its full address (and so any key in the query) at INFO.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
MAX_BYTES = 5 * 1024 * 1024


class SourceError(Exception):
    def __init__(self, kind, source, status=None):
        super().__init__(f'{source}: {kind}' + (f' (HTTP {status})' if status else ''))
        self.kind, self.source, self.status = kind, source, status


def get_json(url, *, params=None, headers=None, seconds=15, source='source', transport=None):
    """GET a JSON answer. Params (which may hold a key) and the answer are never logged."""
    where = urlsplit(url)
    shown = f'{where.scheme}://{where.netloc}{where.path}'
    if where.scheme != 'https':
        raise ValueError('Outside sources are called over HTTPS only.')
    if where.query:
        raise ValueError('Give query values in params, so secret ones are never logged.')
    deadline = time.monotonic() + 2 * seconds
    try:
        with httpx.Client(
            timeout=seconds, trust_env=False, follow_redirects=False, transport=transport
        ) as client:
            with client.stream(
                'GET', url, params=params, headers={'User-Agent': 'universal-agents', **(headers or {})}
            ) as response:
                status = response.status_code
                if status >= 400:
                    log.info('outside source answered: source=%s address=%s status=%s', source, shown, status)
                    raise SourceError(_kind(status), source, status)
                body = b''
                for chunk in response.iter_bytes():
                    body += chunk
                    if time.monotonic() > deadline:
                        # A source that trickles bytes would reset each read timeout forever.
                        log.info('outside source too slow: source=%s address=%s', source, shown)
                        raise SourceError('unavailable', source)
                    if len(body) > MAX_BYTES:
                        raise SourceError('bad_response', source, status)
    except SourceError:
        raise
    except httpx.HTTPError as exc:
        # httpx messages can contain the full address with its query: log only the kind.
        log.info(
            'outside source did not answer: source=%s address=%s error=%s', source, shown, type(exc).__name__
        )
        raise SourceError('unavailable', source) from None
    if 300 <= status < 400:
        raise SourceError('rejected', source, status)
    try:
        return json.loads(body)
    except ValueError:
        raise SourceError('bad_response', source, status) from None


def _kind(status):
    if status in (401, 403):
        return 'unauthorized'
    if status == 429:
        return 'rate_limited'
    if status >= 500:
        return 'unavailable'
    return 'rejected'
