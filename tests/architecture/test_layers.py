"""The layer rules in docs/architecture.md, checked on every run."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / 'agents_service'


def imports_of(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def files_in(folder):
    return [p for p in (PACKAGE / folder).rglob('*.py')]


def test_core_uses_no_higher_layer():
    for path in files_in('core'):
        for module in imports_of(path):
            assert not module.startswith(
                ('agents_service.agents', 'agents_service.api', 'agents_service.worker', 'agents_service.ai')
            ), f'{path.name} imports {module}'


def test_shared_tools_use_only_core():
    for folder in ('ai', 'documents', 'lookups'):
        for path in files_in(folder):
            for module in imports_of(path):
                assert not module.startswith(
                    ('agents_service.agents', 'agents_service.api', 'agents_service.worker')
                ), f'{folder}/{path.name} imports {module}'


def test_agents_never_import_each_other_or_entry_points():
    agents = [p for p in (PACKAGE / 'agents').iterdir() if p.is_dir() and not p.name.startswith('__')]
    for folder in agents:
        for path in folder.rglob('*.py'):
            for module in imports_of(path):
                assert not module.startswith(('agents_service.api', 'agents_service.worker')), (
                    f'{folder.name}/{path.name} imports {module}'
                )
                if module.startswith('agents_service.agents.'):
                    part = module.split('.')[2]
                    assert part in (folder.name, 'base', 'records'), (
                        f'{folder.name}/{path.name} imports {module}'
                    )


def test_only_ai_talks_to_a_model():
    for path in PACKAGE.rglob('*.py'):
        if path.parent.name == 'ai':
            continue
        text = path.read_text()
        assert 'chat/completions' not in text and 'import anthropic' not in text, path


def test_only_lookups_call_outside_sources_from_agents():
    """Agents call outside sources through lookups/http.py (time limits, no keys in logs)."""
    for path in (PACKAGE / 'agents').rglob('*.py'):
        for module in imports_of(path):
            top = module.split('.')[0]
            assert top not in ('httpx', 'requests', 'urllib3', 'aiohttp'), f'{path.name} imports {module}'


def test_agents_never_import_the_wiring_or_the_cli():
    for path in (PACKAGE / 'agents').rglob('*.py'):
        for module in imports_of(path):
            bad = module.startswith(('agents_service.cli', 'agents_service.services'))
            assert not bad, f'{path.name} imports {module}'


def test_only_storage_touches_s3():
    for path in PACKAGE.rglob('*.py'):
        if path.name != 'storage.py':
            assert 'boto3' not in path.read_text(), path


def test_every_registered_agent_follows_the_contract():
    from agents_service.agents import registry
    from agents_service.agents.base import Agent

    for name in registry.AVAILABLE:
        agent = registry.load(name)
        assert isinstance(agent, Agent)
        assert agent.name == name and agent.key_prefix and agent.sections and agent.reader_version
        assert agent.input_kind in ('files', 'data')
