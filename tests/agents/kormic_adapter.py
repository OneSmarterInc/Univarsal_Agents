"""Lets the reader tests moved from Kormic call the agents the way they called Kormic's engine.

Kormic's ``extract(paths, kind, subject, provider)`` read files, asked the model
(``provider(text, schema)``), checked the answer and built records. Here the same
call runs the CV or LinkedIn agent. Kormic's own name check (``identity``) stays in
Kormic, so it is not part of this result.
"""

from pathlib import Path
from unittest.mock import patch

from agents_service.agents.base import InputFile, JobContext
from agents_service.agents.records import job_state

CONFIG = {
    'provider': 'ollama',
    'model': 'test-model',
    'base_url': 'http://127.0.0.1:11434/v1',
    'api_key': '',
    'structured_mode': 'json_schema',
    'reasoning_effort': 'none',
    'context_tokens': 16384,
    'output_tokens': 4096,
    'allow_http': False,
    'request_seconds': 90,
    # Kormic's tuned behaviour, so these tests prove the reader is unchanged.
    'text_first': False,
    'chunk_chars': 1800,
    'file_seconds': 600,
}


def configuration(task=None):
    return dict(CONFIG)


def read_document(path, clean=None):
    """The real reader; tests may patch this to supply text directly."""
    from agents_service.documents import reading

    return reading.read_document(path, clean=clean)


def model_extract(text, schema, config=None):  # replaced with patch() by the tests
    raise AssertionError('patch model_extract in the test')


STATUS = {'done': 'ready', 'partial': 'partial', 'failed': 'failed'}


def extract(paths, kind, subject=None, provider=None, identify=None):
    from agents_service.agents import registry
    from tests.agents import kormic_adapter

    agent = registry.load(kind)
    module = f'agents_service.agents.{kind}.agent'
    ask = provider or kormic_adapter.model_extract
    files = [
        InputFile(index=i, name=Path(p).name, path=Path(p), sha256=f'sha-{i}') for i, p in enumerate(paths)
    ]
    with (
        patch(
            module + '.read_text',
            side_effect=lambda path, settings, clean=None: kormic_adapter.read_document(path),
        ),
        patch(module + '.ask_model', side_effect=lambda text, config: ask(text, None)),
    ):
        result = agent.run(
            JobContext(
                job_id='job_test',
                project='test',
                files=files,
                data={},
                options={},
                settings=None,
                model=CONFIG,
            )
        )
    return {
        'status': STATUS[job_state(result.records, result.files)],
        'records': result.records,
        'files': result.files,
        'warnings': result.warnings,
    }
