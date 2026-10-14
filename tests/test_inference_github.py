import base64
import hashlib
import json
import time

import httpx
import pytest

from extraction_service.db import OAuthState
from extraction_service.errors import ServiceError
from extraction_service.github import GitHubClient, GitHubConnections, analyze_repositories, eligible, redact
from extraction_service.inference import Ollama
from extraction_service.schemas import Observations


def model_client(settings, handler):
    return Ollama(
        settings, httpx.Client(base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handler))
    )


def test_missing_model_digest_fails_closed(service):
    service.settings.ollama_digest = ""
    model = model_client(service.settings, lambda r: pytest.fail("Network must not be attempted"))
    with pytest.raises(ServiceError, match="model_digest_not_configured"):
        model.ready()


def test_readiness_verifies_identity_and_digest(service):
    def handler(r):
        if r.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": service.settings.ollama_model, "digest": "a" * 64}]}
            )
        return httpx.Response(
            200,
            json={"details": {"family": "qwen3", "parameter_size": "1.7B", "quantization_level": "Q4_K_M"}},
        )

    assert model_client(service.settings, handler).ready()["digest"] == "a" * 64


def test_schema_retry_bounded_and_no_paid_fallback(service):
    paths = []

    def handler(r):
        paths.append(str(r.url))
        payload = json.loads(r.content)
        assert payload["think"] is False and payload["stream"] is False
        assert isinstance(payload["format"], dict)
        return httpx.Response(
            200, json={"done": True, "done_reason": "stop", "message": {"content": "bad json"}}
        )

    model = model_client(service.settings, handler)
    with pytest.raises(ServiceError, match="invalid_model_output"):
        model.extract("cv", {"id": "b", "text": "Alex Example"})
    assert paths == ["http://127.0.0.1:11434/api/chat"] * 2


def test_model_timeout_has_no_immediate_retry(service):
    calls = []

    def handler(r):
        calls.append(r)
        raise httpx.ReadTimeout("synthetic", request=r)

    with pytest.raises(ServiceError, match="model_timeout"):
        model_client(service.settings, handler).extract("linkedin", {"id": "b", "text": "Alex Example"})
    assert len(calls) == 1


def test_truncated_model_output_rejected(service):
    model = model_client(
        service.settings,
        lambda r: httpx.Response(
            200, json={"done": True, "done_reason": "length", "message": {"content": "{}"}}
        ),
    )
    with pytest.raises(ServiceError, match="invalid_model_output"):
        model.extract("cv", {"id": "b", "text": "Alex Example"})


@pytest.mark.parametrize("path", ["https://evil.test/x", "//evil.test/x", "/../x", "/x?token=y"])
def test_github_ssrf_path_rejected(path):
    gh = GitHubClient(
        "synthetic",
        httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(lambda r: pytest.fail("No network")),
        ),
    )
    with pytest.raises(ServiceError, match="invalid_github_path"):
        gh.get(path)


def test_github_rate_reset():
    gh = GitHubClient(
        "synthetic",
        httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "42"})),
        ),
    )
    with pytest.raises(ServiceError) as err:
        gh.get("/user")
    assert err.value.retry_after == 42 and err.value.retryable


def test_source_filters_and_redaction():
    for p in [
        ".env",
        "src/secrets.json",
        "node_modules/lib/a.js",
        "package-lock.json",
        "a.key",
        "../src/app.py",
    ]:
        assert not eligible(p)
    assert eligible("src/app.py")
    assert "secret_value" not in redact("api_key=secret_value")


def test_github_metadata_survives_model_outage():
    sha = "a" * 40

    class GH:
        def inventory(self, page):
            return [{"id": 1, "full_name": "synthetic/example", "name": "example", "private": False}]

        def get(self, path, params=None):
            if path == "/user":
                return {"id": 7, "login": "synthetic"}
            if path.endswith("/languages"):
                return {"Python": 100}
            if path.endswith("/commits"):
                return [{"sha": sha, "author": {"login": "synthetic"}}]
            if "/git/trees/" in path:
                return {"tree": [{"type": "blob", "path": "app.py", "size": 20, "sha": sha}]}
            if "/git/blobs/" in path:
                return {
                    "size": 20,
                    "encoding": "base64",
                    "content": base64.b64encode(b"# Python example").decode(),
                }
            raise AssertionError(path)

    class Offline:
        def extract(self, *args):
            raise ServiceError("inference_unavailable", 503, True)

    result = analyze_repositories(
        GH(), 7, {"id": "g", "connection_id": "c", "repository_ids": [1]}, Offline()
    )
    assert result["state"] == "partial"
    assert result["data"]["repositories"][0]["languages"] == {"Python": 100}
    assert result["data"]["repositories"][0]["coverage"]["commit_sha"] == sha


def test_private_repositories_cannot_be_selected():
    class GH:
        def get(self, *args):
            return {"id": 7}

        def inventory(self, page):
            return [{"id": 1, "private": True}]

    with pytest.raises(ServiceError, match="invalid_repository_selection"):
        analyze_repositories(GH(), 7, {"repository_ids": [1]}, object())


def test_oauth_state_consumed_even_when_exchange_fails(service, actor, monkeypatch):
    state = "synthetic-one-time-state"
    digest = hashlib.sha256(state.encode()).hexdigest()
    with service.db.transaction() as db:
        db.add(OAuthState(digest=digest, tenant=actor.tenant, key_id=actor.key_id, expires=time.time() + 600))

    def fail(*args, **kwargs):
        raise httpx.ConnectError("synthetic")

    monkeypatch.setattr(httpx.Client, "post", fail)
    connections = GitHubConnections(service)
    with pytest.raises(ServiceError, match="github_oauth_exchange_failed"):
        connections.callback("code", state)
    with pytest.raises(ServiceError, match="invalid_oauth_state"):
        connections.callback("code", state)


def test_actual_schema_response_parsed(service):
    model = model_client(
        service.settings,
        lambda r: httpx.Response(
            200,
            json={"done": True, "done_reason": "stop", "message": {"content": '{"scalars":[],"records":[]}'}},
        ),
    )
    assert isinstance(model.extract("cv", {"id": "b", "text": "No identity stated"}), Observations)
