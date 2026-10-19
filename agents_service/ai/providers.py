"""Model connections.

Two kinds of provider share one interface:

* ``ollama`` / ``openai``: any OpenAI-compatible server (Ollama on a laptop or
  server, vLLM on a GPU, or a hosted OpenAI-compatible API with a key).
* ``anthropic``: the Anthropic API with an API key.

Agents never talk to a model directly; they call ``provider(config)`` with the
settings from ``model_config(settings, agent)``. Every request is timed, and the
worker can stop a cancelled job between requests (see ``job_scope``).
"""

import contextvars
import ipaddress
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

LOCAL_PROVIDERS = ('ollama', 'openai')


class AIError(Exception):
    """A model problem with a fixed reason; never carries document text."""

    def __init__(self, reason, status=None, field=None):
        self.field = field
        self.reason = reason
        self.status_code = status
        super().__init__(reason)

    @property
    def public_message(self):
        if self.reason == 'context' and self.field == 'CV length':
            return (
                'This CV is too long for the configured model. Upload a shorter CV '
                '(about 4 pages) or configure a model with a larger context.'
            )
        return {
            'context': 'The document is too long for the configured model context.',
            'configuration': 'The model provider is not configured correctly.',
            'connection': 'The model server could not be reached.',
            'timeout': 'The model took too long to answer.',
            'length': 'The model answer exceeded its limit.',
            'format': 'The model returned an answer in an unexpected format.',
            'identity_ambiguous': 'The document owner name could not be resolved reliably.',
            'quality': 'The model answer failed the content or evidence checks.',
            'capability': 'The model server does not support the required answer format.',
            'cancelled': 'The job was cancelled.',
        }.get(
            self.reason,
            {
                401: 'The model provider rejected its API key.',
                403: 'The model provider denied access.',
                404: 'The model or its address was not found.',
                429: 'The model provider is limiting requests. Retry shortly.',
            }.get(self.status_code, 'The model provider is temporarily unavailable.'),
        )


# --- job scope: cancellation and timing --------------------------------------


@dataclass
class RequestStats:
    requests: int = 0
    seconds: float = 0.0
    by_question: dict = field(default_factory=dict)

    def add(self, name, seconds):
        self.requests += 1
        self.seconds += seconds
        entry = self.by_question.setdefault(name, {'requests': 0, 'seconds': 0.0})
        entry['requests'] += 1
        entry['seconds'] = round(entry['seconds'] + seconds, 3)

    def summary(self):
        return {
            'requests': self.requests,
            'seconds': round(self.seconds, 3),
            'by_question': self.by_question,
        }


_check = contextvars.ContextVar('model_check', default=None)
_stats = contextvars.ContextVar('model_stats', default=None)


@contextmanager
def job_scope(check=None, stats=None):
    """Run a job's model requests with a cancel check and a timing record."""
    check_token, stats_token = _check.set(check), _stats.set(stats)
    try:
        yield
    finally:
        _check.reset(check_token)
        _stats.reset(stats_token)


def _before_request():
    check = _check.get()
    if check is not None and not check():
        raise AIError('cancelled')


def _after_request(name, started):
    seconds = time.monotonic() - started
    stats = _stats.get()
    if stats is not None:
        stats.add(name, seconds)
    log.info('model request: question=%s seconds=%.2f', name, seconds)


# --- configuration ------------------------------------------------------------


def model_config(settings, agent):
    """The provider configuration for one agent, validated."""
    values = settings.model_for(agent)
    config = {
        'provider': values['provider'],
        'model': values['name'],
        'base_url': values['base_url'],
        'api_key': values['api_key'],
        'structured_mode': values['structured_mode'],
        'reasoning_effort': values['reasoning_effort'],
        'context_tokens': int(values['context_tokens']),
        'output_tokens': int(values['output_tokens']),
        'allow_http': bool(values['allow_http']),
        'request_seconds': int(values['request_seconds']),
        'text_first': bool(values['text_first']),
        'file_seconds': settings.file_seconds,
        'chunk_chars': settings.cv_chunk_chars,
    }
    validate_configuration(config)
    return config


def focused_reader(config):
    """Small local models read a document section by section; large API models in one request."""
    return config['provider'] in LOCAL_PROVIDERS


def validate_configuration(config):
    if not config.get('model'):
        raise AIError('configuration', field='AGENTS_MODEL_NAME')
    if config.get('context_tokens', 0) < 4096 or not (
        128 <= config.get('output_tokens', 0) < config['context_tokens']
    ):
        raise AIError('configuration', field='AGENTS_MODEL_CONTEXT_TOKENS / OUTPUT_TOKENS')
    if config['provider'] == 'anthropic':
        if not config.get('api_key'):
            raise AIError('configuration', field='AGENTS_MODEL_API_KEY')
        return
    if config['provider'] not in LOCAL_PROVIDERS:
        raise AIError('configuration', field='AGENTS_MODEL_PROVIDER')
    try:
        url = urlsplit(config.get('base_url', ''))
    except ValueError as exc:
        raise AIError('configuration', field='AGENTS_MODEL_BASE_URL') from exc
    if (
        url.scheme not in ('http', 'https')
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not url.path.rstrip('/').endswith('/v1')
    ):
        raise AIError('configuration', field='AGENTS_MODEL_BASE_URL')
    try:
        loopback = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = False
    # A model server on another machine needs HTTPS, unless an operator
    # allows plain HTTP inside a private network (for example a Docker network).
    if not loopback and url.scheme != 'https' and not config.get('allow_http'):
        raise AIError('configuration', field='AGENTS_MODEL_ALLOW_HTTP / HTTPS model address')
    if config.get('structured_mode') not in ('json_schema', 'tools'):
        raise AIError('configuration', field='AGENTS_MODEL_STRUCTURED_MODE')


def request_upper_bound(system, messages, schema=None):
    # Conservative byte-based bound, not a tokenizer. No document text is logged.
    return len(json.dumps([system, messages, schema], ensure_ascii=False).encode('utf-8')) + 512


def strict_json(text):
    """JSON without NaN or Infinity, which are not JSON and break result storage."""

    def reject(constant):
        raise ValueError('non-finite number')

    return json.loads(text, parse_constant=reject)


def check_budget(config, system, messages, max_tokens, schema=None):
    output = min(max_tokens, int(config.get('output_tokens', max_tokens)))
    if request_upper_bound(system, messages, schema) + output > int(config['context_tokens']):
        raise AIError('context')
    return output


@dataclass
class Completion:
    text: str = ''
    data: dict | None = None
    finish: str = 'stop'


# --- providers ----------------------------------------------------------------


class AnthropicProvider:
    def __init__(self, config, timeout):
        self.config, self.timeout = config, timeout

    def __enter__(self):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise AIError('configuration', field='install the "anthropic" package') from exc
        self.manager = Anthropic(
            api_key=self.config['api_key'],
            base_url='https://api.anthropic.com',
            timeout=self.timeout,
            max_retries=0,
        )
        self.client = self.manager.__enter__()
        return self

    def __exit__(self, *args):
        return self.manager.__exit__(*args)

    def generate(
        self, *, system, messages, max_tokens, schema=None, name='output', temperature=None, seed=None
    ):
        _before_request()
        max_tokens = check_budget(self.config, system, messages, max_tokens, schema)
        kwargs = dict(model=self.config['model'], system=system, messages=messages, max_tokens=max_tokens)
        if temperature is not None:
            kwargs['temperature'] = temperature
        if schema is not None:
            kwargs.update(
                tools=[
                    {
                        'name': name,
                        'description': 'Return the requested structured result.',
                        'input_schema': schema,
                    }
                ],
                tool_choice={'type': 'tool', 'name': name},
            )
        started = time.monotonic()
        try:
            reply = self.client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - SDK errors become fixed reasons
            raise _anthropic_error(exc) from exc
        finally:
            _after_request(name, started)
        if reply.stop_reason == 'max_tokens':
            return Completion(finish='length')
        if schema is not None:
            blocks = [b for b in reply.content if b.type == 'tool_use' and b.name == name]
            if reply.stop_reason != 'tool_use' or len(blocks) != 1 or not isinstance(blocks[0].input, dict):
                raise AIError('format')
            return Completion(data=blocks[0].input)
        text = '\n'.join(b.text for b in reply.content if b.type == 'text').strip()
        if reply.stop_reason not in ('end_turn', 'stop_sequence') or not text:
            raise AIError('format')
        return Completion(text=text)


def _anthropic_error(exc):
    name = type(exc).__name__
    if 'Timeout' in name:
        return AIError('timeout')
    if 'Connection' in name:
        return AIError('connection')
    return AIError('provider', getattr(exc, 'status_code', None))


class OpenAICompatibleProvider:
    def __init__(self, config, timeout):
        self.config, self.timeout = config, timeout

    def __enter__(self):
        key = self.config.get('api_key', '')
        self.client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
            headers={'Authorization': 'Bearer ' + key} if key else {},
            trust_env=False,
        )
        return self

    def __exit__(self, *args):
        self.client.close()

    def _body(self, system, messages, max_tokens, schema, name, temperature, seed):
        body = {
            'model': self.config['model'],
            'messages': [{'role': 'system', 'content': system}, *messages],
            'max_tokens': max_tokens,
            'stream': False,
        }
        if temperature is not None:
            body['temperature'] = temperature
        if seed is not None:
            body['seed'] = seed
        if self.config.get('reasoning_effort'):
            body['reasoning_effort'] = self.config['reasoning_effort']
        if schema is not None:
            if self.config['structured_mode'] == 'tools':
                body.update(
                    tools=[
                        {
                            'type': 'function',
                            'function': {
                                'name': name,
                                'description': 'Return the requested structured result.',
                                'parameters': schema,
                            },
                        }
                    ],
                    tool_choice={'type': 'function', 'function': {'name': name}},
                )
            else:
                body['response_format'] = {
                    'type': 'json_schema',
                    'json_schema': {'name': name, 'strict': True, 'schema': schema},
                }
        return body

    def generate(
        self, *, system, messages, max_tokens, schema=None, name='output', temperature=None, seed=None
    ):
        _before_request()
        max_tokens = check_budget(self.config, system, messages, max_tokens, schema)
        body = self._body(system, messages, max_tokens, schema, name, temperature, seed)
        started = time.monotonic()
        try:
            with self.client.stream(
                'POST', self.config['base_url'] + '/chat/completions', json=body
            ) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise AIError('length')
                    chunks.append(chunk)
                raw = b''.join(chunks)
            if response.status_code != 200:
                raise AIError(
                    'capability' if response.status_code in (400, 422) and schema else 'provider',
                    response.status_code,
                )
            return self._completion(strict_json(raw), schema, name)
        except httpx.TimeoutException as exc:
            raise AIError('timeout') from exc
        except httpx.HTTPError as exc:
            raise AIError('connection') from exc
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as exc:
            raise AIError('format') from exc
        finally:
            _after_request(name, started)

    def _completion(self, payload, schema, name):
        choices = payload.get('choices', [])
        if len(choices) != 1:
            raise AIError('format')
        choice = choices[0]
        finish = choice.get('finish_reason')
        message = choice.get('message', {})
        if finish == 'length':
            return Completion(finish='length')
        if schema is not None and self.config['structured_mode'] == 'tools':
            calls = message.get('tool_calls', [])
            if finish != 'tool_calls' or len(calls) != 1 or calls[0]['function']['name'] != name:
                raise AIError('format')
            data = strict_json(calls[0]['function']['arguments'])
        elif schema is not None:
            if finish != 'stop':
                raise AIError('format')
            data = strict_json(message.get('content', ''))
        else:
            text = message.get('content')
            if finish != 'stop' or not isinstance(text, str) or not text.strip():
                raise AIError('format')
            return Completion(text=text.strip())
        if not isinstance(data, dict):
            raise AIError('format')
        return Completion(data=data)


def provider(config, timeout=45):
    validate_configuration(config)
    cls = AnthropicProvider if config['provider'] == 'anthropic' else OpenAICompatibleProvider
    return cls(config, timeout)


def check_model(config, timeout=30):
    """A tiny request that proves the model answers. Used by readiness and `agents try`."""
    with provider(config, timeout=timeout) as client:
        reply = client.generate(
            system='Reply with the single word OK.',
            messages=[{'role': 'user', 'content': 'OK?'}],
            max_tokens=16,
            name='readiness',
        )
    return bool(reply.text)
