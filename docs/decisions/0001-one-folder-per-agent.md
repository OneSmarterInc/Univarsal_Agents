# 1. One folder per agent, with a registry

**Decision:** each agent is a folder with an `agent.py`; the API, keys, limits and docs are built from it.
**Why:** new agents for other projects can be added without touching shared code or existing agents.
