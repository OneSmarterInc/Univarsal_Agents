# 6. Qwen by default, switchable to an API key

**Decision:** Ollama/Qwen is the default provider; `anthropic` (and any OpenAI-compatible server such as vLLM) can be chosen per agent by settings.
**Why:** the owner wants Qwen now and the option to switch later without code changes.
