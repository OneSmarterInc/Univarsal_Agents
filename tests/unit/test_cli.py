"""The `agents try` command sends files or JSON data the way a project would."""

import json

import httpx
import pytest

from agents_service.cli import main as cli


def test_try_needs_files_or_data_but_not_both():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(['try', '--agent', 'cv', '--key', 'k'])
    with pytest.raises(SystemExit):
        parser.parse_args(['try', '--agent', 'cv', '--key', 'k', '--file', 'a.pdf', '--data', '{}'])
    assert parser.parse_args(['try', '--agent', 'x', '--key', 'k', '--data', '{"number": "1"}']).data


def test_try_sends_data_as_json(monkeypatch, capsys):
    sent = []

    def handler(request):
        sent.append(request)
        if request.method == 'POST':
            return httpx.Response(
                202, json={'id': 'job_1', 'state': 'queued', 'links': {'self': '/v1/x/jobs/job_1'}}
            )
        return httpx.Response(200, json={'id': 'job_1', 'state': 'done', 'result': {'records': []}})

    real = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cli.time, 'sleep', lambda s: None)
    code = cli.main(['try', '--agent', 'x', '--key', 'ua_k', '--data', '{"number": "1"}'])
    assert code == 0
    assert json.loads(sent[0].content) == {'data': {'number': '1'}}
    assert sent[0].headers['content-type'] == 'application/json'
    assert '"state": "done"' in capsys.readouterr().out


def test_try_refuses_bad_json(capsys):
    assert cli.main(['try', '--agent', 'x', '--key', 'k', '--data', '{not json']) == 2
