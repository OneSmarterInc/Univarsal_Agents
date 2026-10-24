# Lookup agents: NPPES, OIG, SAM and GitHub

These agents take JSON instead of files and look a person up in an outside source.
They call no model. Each answer says how each fact is known (`details.method`), and
a list search (`details.shape: screens`) never says "clear".

| Agent | Source | Answers | Needs |
|---|---|---|---|
| `nppes` | CMS NPI registry (live API) | The NPI's status, type, issue date, name; what the provider typed in (credential, taxonomy, licence numbers) | Nothing |
| `oig` | HHS-OIG exclusion list (LEIE, a monthly CSV) | `no_match`, `possible_match` or `match` against US federal healthcare exclusions | The list, loaded by an operator |
| `sam` | SAM.gov exclusions (live API) | The same three outcomes against US federal contracting exclusions | A SAM.gov API key |
| `github` | GitHub (live API) | A public account: profile, repositories, languages, activity, and whether it is proved to be the person's | Nothing (a token for more than 60 calls an hour) |

The project decides what an answer means for its person: the agents never compare a
name with a profile, and a `possible_match` always needs a person to check it.

## Switch them on

```
AGENTS_ENABLED_AGENTS=cv,linkedin,nppes,oig,sam,github
```

Give keys to the projects that need them (`agents create-key --project kormic --agent oig`).
Run them on their own worker so they never wait behind CV jobs (they take seconds; a CV
takes minutes):

```
agents worker --agents cv,linkedin
agents worker --agents nppes,oig,sam,github
```

`GET /health/ready` (no key needed) shows each agent as `ok` or `needs setup`. The
reason, for example `not configured: AGENTS_SAM_API_KEY`, is in `state` of each agent in
`GET /v1/agents` (needs a key). One agent not being ready never makes the whole service
"not ready".

**Sending JSON from Windows.** PowerShell changes quotes inside `--data '...'`, so on
Windows put the JSON in a file (save it as UTF-8) and use `--data-file`:

```powershell
Set-Content -Encoding utf8 person.json '{"full_name": "Amara Okafor", "date_of_birth": "1984-02-11"}'
agents try --agent oig --key <key> --data-file person.json
```

On Linux and macOS `--data '{"npi": "1234567893"}'` works as shown below.

## NPPES

No setup. Optional settings: `AGENTS_NPPES_TIMEOUT_SECONDS` (15),
`AGENTS_NPPES_CACHE_HOURS` (24; the same NPI is asked once a day).

```
agents try --agent nppes --key <key> --data '{"npi": "1234567893"}'
```

A number without ten digits or a matching check digit is refused at once (422) and
never sent to CMS. "No such NPI" comes back `done` with an `npi` record whose
`found` is `false`. Licence numbers come back as `claimed_registration` records,
`self_attested`: CMS does not check them, and an active NPI is not an active licence.

## OIG

OIG publishes the full list as `UPDATED.csv` once a month at
https://oig.hhs.gov/exclusions/exclusions_list.asp. Load it on any machine that uses the
service's database and storage:

```
agents tool oig load --file UPDATED.csv --as-of 2026-10-01
agents tool oig status
```

`--as-of` is the date OIG published the file (shown on that page), not today. The file is
checked (it must have the LEIE columns), stored once and used by every worker; each worker
reads it on its next OIG job (about 4 s and 130 MB for 84,000 rows; then about 50 ms per
screen). Until a list is loaded, OIG jobs fail with `not_configured`.

Load the new list every month. A list older than `AGENTS_OIG_MAX_AGE_DAYS` (45) still
screens, but every answer warns, and `agents tool oig status` and `GET /v1/agents` show its age.

```
agents try --agent oig --key <key> --data '{"full_name": "Amara Okafor", "date_of_birth": "1984-02-11"}'
```

Send what you have: `full_name`, `previous_names` (an exclusion is listed under the
name held at the time), `date_of_birth`, `npi`. Each name needs a first name (or an
initial) and a last name; "Smith" alone is refused (422), because the list is searched on
both; a one-word firm ("Acme") is refused for the same reason, so send the firm's full name. A listing with only an initial ("DOE, J"), a one-letter last name or no first
name is still found. "O'Brien" and "Abad-Santos" match however the list writes them. A name alone can only reach
`possible_match`; a matching date of birth or NPI makes it a `match`; a different date
of birth rules a record out.

## SAM

Get a key: sam.gov, Account Details, Public API Key. A personal key allows only
**10 calls a day**; production needs a System Account key with a higher limit.

```
AGENTS_SAM_API_KEY=<key>
AGENTS_SAM_DAILY_LIMIT=10          # shared by every worker; each page of 10 is one call
AGENTS_SAM_PROJECT_DAILY_LIMIT=    # optional: calls one project may use per day
AGENTS_SAM_CACHE_HOURS=24          # the same name is asked once a day
AGENTS_SAM_TIMEOUT_SECONDS=60      # SAM.gov's search often takes 20-40 s
AGENTS_SAM_MAX_PAGES=5             # more matches than 5 x 10 is source_incomplete
AGENTS_SAM_MAPPING_VERIFIED=false  # set true after checking one live answer
```

The key is sent only to SAM.gov. It is never logged or stored in a result. Dates come
back as `YYYY-MM-DD`; an end date of `indefinite` means the exclusion has no end date
(SAM.gov writes it as a date in 2101).

SAM.gov joins the words of a name search with AND, so only the first and last name
words are sent ("Dr Jane Marie Smith Jr" is searched as "jane smith"); every candidate
is then compared with the full name here. Titles, suffixes, credentials and middle names
never hide a listing ("Jane Smith, LCSW" finds "SMITH, JANE ELIZABETH"), and a firm is
compared without its company type ("Acme Health LLC" finds "ACME HEALTH INC"). A name of
titles only ("Dr. Jr.") is refused (422). GSA serves at most 10 records per page.

**Before relying on SAM:** the field mapping comes from GSA's published sample. Make
one live call with a name from https://sam.gov/search/?index=ex, check the hit's name,
agency and dates in the result, then set `AGENTS_SAM_MAPPING_VERIFIED=true`. Until then
every answer says the mapping is unchecked.

SAM fails closed: an answer it cannot read safely, more matches than it can read, a
refused key or a used-up daily limit end the job with an error code, never `no_match`.

## GitHub

```
AGENTS_GITHUB_TOKEN=            # optional: a token with no scopes (5,000 calls an hour, not 60)
AGENTS_GITHUB_DAILY_LIMIT=4000  # calls per day shared by every worker (a job makes 2 to 4)
AGENTS_GITHUB_CACHE_HOURS=24    # public account data is reused this long; ownership never
AGENTS_GITHUB_MAX_REPOS=100     # repositories read per account, most recently pushed first
```

```
agents try --agent github --key <key> --data '{"github": "https://github.com/octocat"}'
```

Input: `github` (a username or any github.com link to the account), and optionally one
proof that the account is the person's:

- `oauth_token`: the person's own token from the project's "Connect GitHub" sign-in (any
  scope, or none). GitHub says whose it is. It is used for one call, deleted with the job
  input and never stored, cached or logged.
- `proof_challenge`: a code of 12–100 letters, digits, `-` or `_` the project gave the person
  to put in a public gist (its description, a file name or its text).

| Section | Holds | `method` |
|---|---|---|
| `github_account` | `found`, username, type (user or organisation), id, url, created on | `primary_source` |
| `github_profile` | name, bio, company, location, blog: typed in by the account holder | `self_attested` |
| `github_activity` | repositories read, original and forked, stars on original work, languages by repository share, last push, followers | `primary_source` |
| `github_repository` | one per original repository (up to 25, most recently pushed first): name, url, language, stars, forks, dates, description, topics | `primary_source` |
| `github_ownership` | `proved_oauth`, `proved_gist` or `not_proved`, how, and the reason | `primary_source` when proved |

A username GitHub does not know ends `done` with one `github_account` record, `found: false`.
Forks are counted but never shown as own work. Private repositories are never read. The
agent does not judge the person or the work: the project does.

## Job errors of lookup agents

| code | Means | Retry? |
|---|---|---|
| `source_unavailable` | The source did not answer (the service already tried twice) | Later |
| `daily_limit_reached` | Today's SAM.gov limit is used up | Tomorrow |
| `not_configured` | Key missing or refused, or no OIG list loaded | After setup |
| `source_rejected` | The source refused the request | No |
| `source_unreadable` | The answer could not be read safely; nothing was checked | No: tell the service owner |
| `source_incomplete` | More matches than can be checked; nothing was checked | No |

## Outbound access (AWS)

Outside calls go over IPv4 (`AGENTS_OUTBOUND_IPV4=true`). Some networks publish IPv6
addresses they cannot reach; Python then waits for IPv6 to time out on every call (a SAM
search took 65 s instead of 2 s on an office laptop). Set it to `false` only on a network
with IPv6 and no IPv4.

Workers that run lookup agents need HTTPS out to `npiregistry.cms.hhs.gov` (NPPES) and
`api.sam.gov` (SAM) and `api.github.com` (GitHub). OIG makes no outside calls.
