# API guide (for project developers)

Call the agents **from your backend only**. Keys must never reach a browser or a
mobile app: requests that carry a browser `Origin` header are refused.

## 1. Get your keys

Ask the agents admin for one key per agent your project uses, for example:

```
CV key:        ua_cv_3f9c0a1b2c3d4e5f.Xy...      (works only on /v1/cv)
LinkedIn key:  ua_li_7a1b2c3d4e5f6a7b.Qw...      (works only on /v1/linkedin)
```

Store each key in your backend settings (for example `AGENTS_CV_KEY`). Send it as
`Authorization: Bearer <key>`.

## 2. The three calls

| Call | Does |
|---|---|
| `POST /v1/<agent>/jobs` | Starts a job with the files. Answers **202** with the job. |
| `GET /v1/<agent>/jobs/{id}` | The job; when finished, its result. |
| `DELETE /v1/<agent>/jobs/{id}` | Cancels a waiting or running job, or deletes a finished result. |

Also: `GET /v1/agents` (the agents and their limits), `GET /health/live`, `GET /health/ready`.

### Start a job

Multipart form:

| Field | Required | Meaning |
|---|---|---|
| `files` | yes | The file(s). Repeat the field for several screenshots. |
| `fields` | no | Comma-separated sections you want, for example `name,skills`. Default: everything. |
| `subject_ref` | no | Your own reference (for example a user id, up to 128 characters). Returned as given. |

Header `Idempotency-Key: <your unique id>` (recommended): if your call is retried,
you get the same job back instead of a second one. Reusing the key with different
files answers 409.

```bash
curl -X POST https://agents.example.com/v1/cv/jobs \
  -H "Authorization: Bearer $AGENTS_CV_KEY" \
  -H "Idempotency-Key: cv-upload-8841" \
  -F files=@cv.pdf -F subject_ref=user-42
```

```json
{"id": "job_5c1f...", "agent": "cv", "state": "queued", "subject_ref": "user-42",
 "queue": {"position": 3, "estimated_wait_seconds": 180}, "poll_after_seconds": 5,
 "links": {"self": "/v1/cv/jobs/job_5c1f..."}}
```

**Lookup agents** (input `data` in `GET /v1/agents`) take JSON instead of files. Their
`data_schema` in `GET /v1/agents` lists the fields:

```bash
curl -X POST https://agents.example.com/v1/<agent>/jobs \
  -H "Authorization: Bearer $AGENTS_KEY" -H "Content-Type: application/json" \
  -d '{"data": {"number": "1234567893"}, "subject_ref": "user-42"}'
```

The data you send is deleted when the job ends; only the result is kept.

### Wait for the result

Call `GET links.self` every `poll_after_seconds` until `state` is no longer
`queued` or `running`. `queue.estimated_wait_seconds` lets you show a wait time.

| State | Meaning |
|---|---|
| `queued` | Waiting in line |
| `running` | Being read |
| `done` | Finished; every file was read |
| `partial` | Finished; some files failed (see `result.files`) |
| `failed` | Nothing could be read (see `result.files` or `error`) |
| `expired` | Waited longer than 30 minutes without starting; send it again |
| `cancelled` | You cancelled it |

### Python example (backend)

```python
import time, httpx

def read_cv(path, key, base='https://agents.example.com'):
    with httpx.Client(base_url=base, headers={'Authorization': f'Bearer {key}'}, timeout=60) as client:
        with open(path, 'rb') as f:
            response = client.post('/v1/cv/jobs', files={'files': (path, f)},
                                   headers={'Idempotency-Key': f'cv-{path}'})
        response.raise_for_status()                               # 4xx: fix the request; 429/503: retry later
        job = response.json()
        while job['state'] in ('queued', 'running'):
            time.sleep(job.get('poll_after_seconds', 5))
            response = client.get(job['links']['self'])
            if response.status_code == 429:                       # too many checks: wait, then retry
                time.sleep(int(response.headers.get('Retry-After', '5')))
                continue
            response.raise_for_status()
            job = response.json()
        return job
```

In a web app, start the job in the request and check it from a background task,
so no user request waits for minutes.

## 3. The result

```json
{
  "state": "done",
  "result": {
    "records": [
      {"id": "4f2a...", "section": "name", "value": "Priya Raman",
       "details": {"value": "Priya Raman"},
       "sources": [{"file_index": 0, "sha256": "9b1c...", "evidence": "Priya Raman\npriya@example.com"}]},
      {"id": "a81e...", "section": "work_experience_entries",
       "value": "Clinical Data Analyst · Riverside Health · 2021 · Present",
       "details": {"title": "Clinical Data Analyst", "organization": "Riverside Health",
                   "start_date": "2021", "end_date": "Present", "description": null,
                   "technologies": [], "url": null},
       "sources": [{"file_index": 0, "sha256": "9b1c...",
                    "evidence": "Clinical Data Analyst, Riverside Health, 2021 - Present"}]}
    ],
    "files": [{"index": 0, "status": "complete", "records": 9}],
    "warnings": [],
    "notices": [],
    "reader_version": "cv/2",
    "model": {"provider": "ollama", "name": "kormic-local"},
    "timing": {"seconds": 74.2, "model": {"requests": 11, "seconds": 70.9}}
  }
}
```

- **records:** one per fact. `evidence` is the exact text from the file. The same
  fact in two files is one record with two `sources`.
- **name records:** every possible owner name is returned. Decide yourself which
  one fits (for example, compare with your user's profile).
- **files:** per file, `complete`, `skipped` (duplicate screenshot) or `failed`
  with a safe `error` message.
- **notices:** `level: action` means the user should choose between alternatives;
  `level: info` explains something left out.
- **reader_version / model:** which reader and model produced this result.
- **Lookup agents:** each source is `{"source": "<public address>", "retrieved_at": "<when>",
  "evidence": "..."}` instead of a file. A lookup that finds nothing ends `done` with a record
  that says so (for example section `not_found`), never `failed`.

### Sections

| Agent | Sections |
|---|---|
| CV | name, email, phone, location, summary, links, institution, major, graduation_year, gpa, gpa_scale, education, work_experience_entries, projects, publications, certifications, awards, volunteering, skills, languages, other_information |
| LinkedIn | name, headline, about, location, skills, experiences, education, projects, posts, certifications |
| NPPES | npi, full_name, npi_status, npi_type, npi_enumerated_on, credential, taxonomy, practice_state, former_name, claimed_registration |
| OIG | oig_exclusion |
| SAM | sam_exclusion |

The lookup agents (NPPES, OIG, SAM), their input and what their answers mean:
[Lookup agents](lookup-agents.md).

## 4. Errors

Every error has the same shape:

```json
{"error": {"code": "rate_limited", "message": "...", "retryable": true, "retry_after_seconds": 12},
 "request_id": "e3b0..."}
```

| HTTP | code | What to do |
|---|---|---|
| 401 | `invalid_credentials` | Check the key |
| 403 | `wrong_agent` | Use the key made for this agent |
| 403 | `browser_calls_not_allowed` | Call from your backend |
| 404 | `resource_not_found` | Wrong job id, or the job belongs to another key's project/agent |
| 409 | `idempotency_conflict` | Use a new Idempotency-Key for new files |
| 413 / 415 / 422 | `file_too_large`, `unsupported_media_type`, `invalid_document`, `too_many_files`, ... | Fix the file |
| 429 | `rate_limited`, `project_queue_full`, `busy` | Wait `Retry-After` seconds, then retry |
| 503 | `database_unavailable`, `storage_unavailable` | Retry shortly |

A finished job can also carry an `error` (state `failed`):

| code | What to do |
|---|---|
| `no_records` | Nothing could be read from the files; check the file |
| `model_unavailable`, `worker_lost`, `expired` | Send it again later |
| `source_unavailable`, `daily_limit_reached` | Lookup agents: send it again later (tomorrow for the daily limit) |
| `not_configured`, `source_rejected`, `source_unreadable`, `source_incomplete` | Lookup agents: nothing was checked; see [Lookup agents](lookup-agents.md) |
| `internal_error` | Tell the service owner the job id |

## 5. Limits (defaults)

| Limit | Value |
|---|---|
| Requests per key | 60 per minute (starting, cancelling, listing) |
| Job checks per key | 600 per minute (`GET /v1/<agent>/jobs/{id}`) |
| New jobs per key | 10 per minute |
| Waiting jobs per project | 50 |
| File size | 10 MB |
| CV | 1 file, PDF up to 30 pages, text up to 36,000 characters |
| LinkedIn | 1–5 screenshots |
| SAM | The service's SAM.gov daily limit (10 a day with a free key), shared by all projects |
| Waiting before `expired` | 30 minutes |
| Results kept | 7 days after the job ends, then deleted (download what you need) |

`GET /v1/agents` always shows the current limits.

## 6. Versions

`/v1` stays stable. A change that would break callers goes to `/v2`, announced in
the [changelog](changelog.md).
