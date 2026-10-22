"""Model providers: configuration checks, request format, cancellation and timing."""

import json

import httpx
import pytest

from agents_service.ai import providers
from agents_service.ai.providers import AIError, RequestStats, job_scope, model_config, provider
from tests.conftest import make_settings


def config(tmp_path, **overrides):
    return model_config(make_settings(tmp_path, **overrides), 'cv')


def test_default_is_local_ollama(tmp_path):
    cfg = config(tmp_path)
    assert (cfg['provider'], cfg['model'], cfg['base_url']) == (
        'ollama',
        'kormic-local',
        'http://127.0.0.1:11434/v1',
    )
    assert providers.focused_reader(cfg)


def test_remote_model_needs_https_unless_allowed(tmp_path):
    with pytest.raises(AIError):
        config(tmp_path, model_base_url='http://10.0.0.5:11434/v1')
    assert config(tmp_path, model_base_url='http://10.0.0.5:11434/v1', model_allow_http=True)
    assert config(tmp_path, model_base_url='https://models.internal/v1')


def test_bad_addresses_are_refused(tmp_path):
    for url in (
        'ftp://x/v1',
        'http://127.0.0.1:11434',
        'http://user:pw@127.0.0.1/v1',
        'http://127.0.0.1/v1?x=1',
    ):
        with pytest.raises(AIError):
            config(tmp_path, model_base_url=url)


def test_anthropic_needs_a_key(tmp_path):
    with pytest.raises(AIError):
        config(tmp_path, model_provider='anthropic', model_name='claude-x')
    cfg = config(tmp_path, model_provider='anthropic', model_name='claude-x', model_api_key='sk-test')
    assert not providers.focused_reader(cfg)


def fake_server(monkeypatch, reply, seen):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    real = httpx.Client

    def client(**kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(providers.httpx, 'Client', client)


def test_openai_compatible_request_and_answer(tmp_path, monkeypatch):
    seen = []
    fake_server(
        monkeypatch,
        {
            'choices': [
                {'finish_reason': 'stop', 'message': {'content': json.dumps({'names': ['Alex Example']})}}
            ]
        },
        seen,
    )
    stats = RequestStats()
    with job_scope(stats=stats), provider(config(tmp_path)) as client:
        reply = client.generate(
            system='s',
            messages=[{'role': 'user', 'content': 'u'}],
            max_tokens=100,
            schema={'type': 'object'},
            name='q',
        )
    assert reply.data == {'names': ['Alex Example']}
    body = seen[0]
    assert body['model'] == 'kormic-local'
    assert body['reasoning_effort'] == 'none'
    assert body['response_format']['type'] == 'json_schema'
    assert stats.requests == 1 and stats.by_question['q']['requests'] == 1


def test_cancelled_job_sends_no_more_requests(tmp_path, monkeypatch):
    seen = []
    fake_server(monkeypatch, {}, seen)
    with job_scope(check=lambda: False), provider(config(tmp_path)) as client:
        with pytest.raises(AIError) as error:
            client.generate(system='s', messages=[], max_tokens=10)
    assert error.value.reason == 'cancelled'
    assert seen == []


def test_unreachable_server_is_a_connection_error(tmp_path):
    cfg = config(tmp_path, model_base_url='http://127.0.0.1:9/v1')
    with provider(cfg, timeout=2) as client, pytest.raises(AIError) as error:
        client.generate(system='s', messages=[{'role': 'user', 'content': 'u'}], max_tokens=10)
    assert error.value.reason == 'connection'


def test_request_larger_than_context_is_refused(tmp_path):
    with provider(config(tmp_path, model_context_tokens=4096)) as client, pytest.raises(AIError) as error:
        client.generate(system='s', messages=[{'role': 'user', 'content': 'x' * 10000}], max_tokens=1000)
    assert error.value.reason == 'context'


def test_vllm_gets_no_reasoning_effort_unless_set(tmp_path, monkeypatch):
    reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'names': []})}}]}
    seen = []
    fake_server(monkeypatch, reply, seen)

    def body_for(**settings):
        seen.clear()
        with provider(config(tmp_path, **settings)) as client:
            client.generate(
                system='s',
                messages=[{'role': 'user', 'content': 'u'}],
                max_tokens=100,
                schema={'type': 'object'},
            )
        return seen[0]

    vllm = {'model_provider': 'openai', 'model_base_url': 'https://gpu.internal/v1'}
    assert 'reasoning_effort' not in body_for(**vllm)
    assert body_for(**vllm, model_reasoning_effort='low')['reasoning_effort'] == 'low'
    assert body_for()['reasoning_effort'] == 'none'  # Qwen on Ollama keeps thinking off


def test_an_anthropic_override_gets_anthropic_limits(tmp_path):
    settings = make_settings(
        tmp_path,
        model_context_tokens=16384,
        model_output_tokens=2048,
        model_overrides={'linkedin': {'provider': 'anthropic', 'name': 'claude-x', 'api_key': 'k'}},
    )
    linkedin = model_config(settings, 'linkedin')
    assert (linkedin['context_tokens'], linkedin['output_tokens']) == (131072, 8000)
    assert model_config(settings, 'cv')['output_tokens'] == 2048  # the global model is unchanged


def test_an_override_never_reuses_the_global_key_on_another_server(tmp_path):
    settings = make_settings(
        tmp_path,
        model_provider='anthropic',
        model_name='claude-x',
        model_api_key='anthropic-key',
        model_overrides={'cv': {'provider': 'openai', 'base_url': 'https://gpu.internal/v1', 'name': 'qwen'}},
    )
    assert model_config(settings, 'cv')['api_key'] == ''
    assert model_config(settings, 'linkedin')['api_key'] == 'anthropic-key'
    moved = make_settings(
        tmp_path,
        model_api_key='ollama-proxy-key',
        model_overrides={'cv': {'base_url': 'https://other.internal/v1'}},
    )
    assert model_config(moved, 'cv')['api_key'] == ''


def test_an_anthropic_override_without_its_own_key_is_a_configuration_error(tmp_path):
    settings = make_settings(
        tmp_path,
        model_api_key='global-key',
        model_overrides={'cv': {'provider': 'anthropic', 'name': 'claude-x'}},
    )
    with pytest.raises(AIError) as error:
        model_config(settings, 'cv')
    assert error.value.reason == 'configuration'
