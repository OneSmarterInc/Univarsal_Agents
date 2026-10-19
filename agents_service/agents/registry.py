"""The list of agents.

To add an agent: create its folder (copy agents/_template), add one line to
AVAILABLE below, then switch it on with AGENTS_ENABLED_AGENTS.
"""

from importlib import import_module

AVAILABLE = {
    'cv': 'agents_service.agents.cv.agent',
    'linkedin': 'agents_service.agents.linkedin.agent',
}

KEY_PREFIX = 'ua_'


def load(name):
    if name not in AVAILABLE:
        raise ValueError(f'Unknown agent "{name}". Available: {", ".join(sorted(AVAILABLE))}')
    agent = import_module(AVAILABLE[name]).AGENT
    if agent.name != name:
        raise ValueError(f'Agent folder "{name}" defines name "{agent.name}".')
    return agent


def enabled(settings):
    """{name: agent} for every switched-on agent, in the order given."""
    return {name: load(name) for name in settings.enabled_agents}
