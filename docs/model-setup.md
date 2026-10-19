# Model setup

The agents use **Qwen on Ollama** by default, through Ollama's OpenAI-compatible
address. Only `agents_service/ai/` talks to a model, so switching is a settings change.

## Default: Ollama on the same machine

If you already use `kormic-local` for Kormic Careers, the agents can use the same
model. The default settings are:

```
AGENTS_MODEL_PROVIDER=ollama
AGENTS_MODEL_BASE_URL=http://127.0.0.1:11434/v1
AGENTS_MODEL_NAME=kormic-local
AGENTS_MODEL_REASONING_EFFORT=none
AGENTS_MODEL_CONTEXT_TOKENS=16384
```

To create the model on a new machine (laptop or server), use the same Modelfile
settings as `kormic-local`:

```
ollama pull qwen3:1.7b
```

Modelfile:

```
FROM qwen3:1.7b
PARAMETER temperature 0
PARAMETER top_k 20
PARAMETER top_p 0.95
PARAMETER num_ctx 16384
PARAMETER repeat_penalty 1
```

```
ollama create kormic-local -f Modelfile
agents model-check
```

`AGENTS_MODEL_CONTEXT_TOKENS` must equal `num_ctx`. Thinking stays off through
`AGENTS_MODEL_REASONING_EFFORT=none`.

## Ollama on another machine

```
AGENTS_MODEL_BASE_URL=https://models.internal.example/v1
```

Use HTTPS. Plain HTTP to another machine is refused unless `AGENTS_MODEL_ALLOW_HTTP=true`,
which is meant only for a private network such as a Docker network.

## Faster: Qwen on a GPU (vLLM)

vLLM serves Qwen on a GPU with the same OpenAI-compatible address and answers many
requests at once. Point the agents at it; nothing else changes:

```
AGENTS_MODEL_PROVIDER=openai
AGENTS_MODEL_BASE_URL=https://gpu-host.internal/v1
AGENTS_MODEL_NAME=Qwen/Qwen3-1.7B
AGENTS_MODEL_API_KEY=<vLLM --api-key value, if set>
```

## Later: an API key model

```
AGENTS_MODEL_PROVIDER=anthropic
AGENTS_MODEL_NAME=<model id>
AGENTS_MODEL_API_KEY=<key>
AGENTS_MODEL_OUTPUT_TOKENS=8000
AGENTS_MODEL_CONTEXT_TOKENS=131072
```

Install the extra once: `pip install -e ".[anthropic]"`. Large API models read a
whole document in one request; small local models read it section by section.

## One agent on a different model

```
AGENTS_MODEL_OVERRIDES={"linkedin": {"provider": "anthropic", "name": "<model id>", "api_key": "<key>"}}
```

Any `model_*` setting can be overridden per agent (`provider`, `base_url`, `name`,
`api_key`, `structured_mode`, `reasoning_effort`, `context_tokens`, `output_tokens`,
`allow_http`, `request_seconds`, `text_first`).

## After any model change

1. `agents model-check`
2. `agents accuracy` and compare the score with the previous run
3. Every result's `model` field shows which model produced it.
