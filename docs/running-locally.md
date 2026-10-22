# Running locally (before AWS)

Test everything on your laptop first; AWS costs money only after this works.

## 1. Install once

| Need | Windows | Linux |
|---|---|---|
| Python 3.12 or newer | python.org installer | `apt install python3.12 python3.12-venv` |
| Tesseract (reads screenshots and scans) | UB Mannheim installer from github.com/UB-Mannheim/tesseract/wiki, then set `AGENTS_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` | `apt install tesseract-ocr` |
| Ollama with your Qwen model | Already set up as `kormic-local` | See [Model setup](model-setup.md) |

PDF pages are rendered by `pypdfium2`, which pip installs; Poppler is not needed.

## 2. Set up the folder

```powershell
cd D:\Kormic_Carrers
git clone -b production-ready https://github.com/OneSmarterInc/Universal_Agents.git
cd Universal_Agents
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[test]"
copy .env.example .env
agents generate-secret
```

Open `.env` and paste the printed value into `AGENTS_KEY_PEPPER`. On Windows also
set `AGENTS_TESSERACT_CMD`. Then:

```powershell
agents init-db
agents model-check
```

`model-check` must print `cv: OK` and `linkedin: OK`.

**Already set up before version 1.1?** The project is now named Universal Agents. Once:

```powershell
git remote set-url origin https://github.com/OneSmarterInc/Universal_Agents.git
git pull
pip install -e ".[test]"
agents init-db
```

`init-db` adds one column to existing jobs; nothing is lost. If not, start Ollama
(`ollama ps`) and check `AGENTS_MODEL_NAME` in `.env`.

## 3. Start it: 3 terminals

Activate `.venv` in each terminal first.

| Terminal | Command | Does |
|---|---|---|
| 1 | Ollama (already running as a Windows app) | The model |
| 2 | `agents serve` | The API on http://127.0.0.1:8000 |
| 3 | `agents worker` | Reads the files |

`Ctrl+C` in the worker stops after the current job; press it twice to stop at once
(the job then goes back to the line).

## 4. Test it: 4 levels

**Level 1: each agent**

```powershell
agents create-key --project test --agent cv
agents create-key --project test --agent linkedin
agents try --agent cv --key <cv key> --file D:\test\my_cv.pdf
agents try --agent linkedin --key <linkedin key> --file shot1.png --file shot2.png
```

`try` uploads, waits and prints the result JSON a project would get.

**Level 2: several projects at once**

- Make keys for two projects (`--project alpha`, `--project beta`), start `try` for both
  at the same time, and check each gets only its own result.
- Start a second worker in a 4th terminal; the two never take the same job.
- Send a CV with the LinkedIn key: it must be refused (`wrong_agent`).
- `agents stats` shows the waiting line and usage per project.

**Level 3: a real project (phase 3)**

Point the project's backend at `http://127.0.0.1:8000` with its keys and use the app.

**Level 4: the exact AWS setup (phase 2)**

Run the Docker compose file from phase 2 on your laptop with Docker Desktop.

## 5. Speed on a laptop

- Plug in the charger and set Windows power mode to **Best performance**. On battery
  saver the CPU is much slower.
- `AGENTS_CV_CHUNK_CHARS=3000` and `AGENTS_MODEL_TEXT_FIRST=true` (the defaults) cut the
  number of model questions. Set them to `1800` and `false` to get Kormic's exact
  earlier behaviour.
- Every result shows `timing.model.requests` and seconds, and the worker logs each
  model question with its time.
- After changing the model or these settings, run `agents accuracy` and compare the score.

## 6. Where things are

| Thing | Place |
|---|---|
| Settings | `.env` (see `.env.example` for every setting) |
| Database | `data/agents.db` (SQLite) |
| Uploaded files while waiting | `data/files/` (deleted when each job ends) |
| Logs | The terminals (no file content is ever logged) |

## 7. For developers

```
agents check                                   # lint + all tests on SQLite
AGENTS_TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/agents_test pytest
```

The second line runs every test on PostgreSQL (the AWS database). Use an empty,
UTF-8 database you do not need: each test wipes it.
