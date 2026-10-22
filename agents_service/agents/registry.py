"""The list of agents.

To add an agent: create its folder (copy agents/_template), add one line to
AVAILABLE below, then switch it on with AGENTS_ENABLED_AGENTS.
"""

import re
from importlib import import_module

AVAILABLE = {
    'cv': 'agents_service.agents.cv.agent',
    'linkedin': 'agents_service.agents.linkedin.agent',
    'nppes': 'agents_service.agents.nppes.agent',
    'oig': 'agents_service.agents.oig.agent',
}

KEY_PREFIX = 'ua_'


def load(name):
    if name not in AVAILABLE:
        raise ValueError(f'Unknown agent "{name}". Available: {", ".join(sorted(AVAILABLE))}')
    agent = import_module(AVAILABLE[name]).AGENT
    if agent.name != name:
        raise ValueError(f'Agent folder "{name}" defines name "{agent.name}".')
    check_definition(agent)
    return agent


def check_definition(agent):
    """Catch a wrong agent definition at start, not at the first request."""
    problems = []
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', agent.name):
        problems.append('name: lower case letters, digits and _, up to 40')
    if not re.fullmatch(r'[a-z0-9]{1,10}', agent.key_prefix or ''):
        problems.append('key_prefix: 1-10 lower case letters or digits')
    if agent.input_kind not in ('files', 'data'):
        problems.append('input_kind: "files" or "data"')
    if agent.input_kind == 'data' and agent.data_model is None:
        problems.append('data agents need a data_model')
    if not agent.sections:
        problems.append('sections: at least one')
    if problems:
        raise ValueError(f'Agent "{agent.name}" is not defined correctly: ' + '; '.join(problems))


def enabled(settings):
    """{name: agent} for every switched-on agent, in the order given."""
    return {name: load(name) for name in settings.enabled_agents}
