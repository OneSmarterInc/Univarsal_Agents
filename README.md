# Universal Agents

Shared **extraction agents** for every OneSmarter project. A project sends a file
to an agent with its API key; the agent reads it and returns everything it found
as JSON, with the exact quote from the file for every fact. Each project then
applies its own rules (for example, Kormic Careers checks the name against the
person's profile).

| Agent | Reads | Address |
|---|---|---|
| CV | 1 PDF or Word CV | `/v1/cv/jobs` |
| LinkedIn | 1–5 profile screenshots (PNG, JPG, WebP) | `/v1/linkedin/jobs` |
| More agents | Add a folder (see [Adding an agent](docs/adding-an-agent.md)) | `/v1/<name>/jobs` |

- **Keys:** each project gets its own key for each agent it uses. A CV key works only on the CV agent.
- **Model:** Qwen on Ollama by default. A setting can switch an agent to an API-key model later.
- **Status:** phase 1 (runs on a laptop or one server). Phase 2 adds Docker and AWS.

## Documentation

| For | Read |
|---|---|
| Project developers calling the agents | [API guide](docs/api-guide.md) |
| Running it on your laptop (Windows or Linux) | [Running locally](docs/running-locally.md) |
| Admins handing out keys | [Keys](docs/keys.md) |
| The model (Ollama, API key, GPU) | [Model setup](docs/model-setup.md) |
| How the code is organised | [Architecture](docs/architecture.md) |
| Adding a new agent | [Adding an agent](docs/adding-an-agent.md) |
| What changed | [Changelog](docs/changelog.md) · [Decisions](docs/decisions/) |

## Run it (short version)

```powershell
pip install -e ".[test]"
copy .env.example .env        # then set AGENTS_KEY_PEPPER (agents generate-secret)
agents init-db
agents serve                  # terminal 1: the API on http://127.0.0.1:8000
agents worker                 # terminal 2: reads the files (Ollama must be running)
agents create-key --project test --agent cv
agents try --agent cv --key <key> --file my_cv.pdf
```

Full steps, including Tesseract and Ollama: [Running locally](docs/running-locally.md).

## Commands

| Command | Does |
|---|---|
| `agents init-db` | Creates or upgrades the database |
| `agents serve` | Starts the API |
| `agents worker [--agents cv,linkedin]` | Starts a worker (start as many as you need) |
| `agents create-key --project P --agent A` | Makes a key (shown once) |
| `agents list-keys` / `agents revoke-key KEY_ID` | Lists or blocks keys |
| `agents try --agent A --key K --file F` (or `--data '{...}'`) | Sends files (or JSON for a lookup agent) like a project would and prints the result |
| `agents stats` | Usage per project and agent, and the waiting line |
| `agents model-check` | Checks the model answers |
| `agents accuracy` | Runs made-up documents through the real model and scores them |
| `agents check` | Lint and all tests (developers) |

## Repository layout

```
agents_service/   the service (api, agents, ai, documents, core, worker, cli)
tests/            all tests (agents check)
accuracy/         made-up documents with expected answers
docs/             guides and decisions
legacy/           the October pilot, kept for reference (GitHub agent comes back in phase 4)
```

All code is OneSmarter code; see [OWNERSHIP.md](OWNERSHIP.md).
