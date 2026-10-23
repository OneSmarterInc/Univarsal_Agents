# Changelog

## 1.3.1 — fixes from the second student CV run (October 2026)

From a laptop run of 7 student CVs: 2 failed on a model timeout, and the others lost
education entries, certificates and roles.

- **A model timeout skips one section only.** A question the small model does not answer
  in time no longer fails the whole CV (it did on 2 of 7 CVs). That section is read from
  the document when its layout is plain, else a note says it was left out. Running out of
  file time keeps what was read. A file fails only when nothing at all was read, or after
  3 timeouts in a row; a job whose files all timed out goes back to the line once, then
  ends as `model_unavailable` (retryable) instead of `no_records`.
- **OCR no longer repeats a page's own text.** A page with pictures is still read by OCR,
  but a line whose words the text layer already holds is left out. One CV was read twice
  (once noisy), which doubled its projects and filed certificates as jobs.
- **Education (reader cv/6):** the degree, grade and school on one line are split
  ("HSC 73.50 % Example Junior College 2024", "SSC-(91.16%)Example Vidya Mandir",
  "… at Example College of / Engineering"); "Secondary School (Class X)" is a degree, not a
  school; a coursework or grade line no longer hides the degree line's dates; a month/year
  date ("08/2024") is kept out of the degree name; Vidyalaya, Vidya Mandir and similar
  names are schools. One unclear entry no longer drops the clear ones.
- **Certificates and awards** are read from their list when the document shows more items
  than the model's checked answer (the model often returned the first item only):
  bullets, OCR bullets, wrapped lines ("Ver. 9.1"), items on the heading line, and
  certificates listed under Achievements. Narrow two-column text and skills lines are left
  to the model.
- **Roles:** "Internship Experience" and "Internships" are work headings; a role with no
  date followed by its bullets is read; the wrapped end of a bullet ("Project Expo") is
  not a heading, and a dated role after an unfinished bullet starts a new entry.

## 1.3.0 — GitHub agent (October 2026)

- **GitHub agent** (`/v1/github/jobs`, keys `ua_gh_...`): reads a public GitHub account
  from a username or any github.com link. Profile (as typed by the holder,
  `self_attested`), account age, original and forked repositories, languages by
  repository, stars on original work, last push, and up to 25 original repositories.
  No model is used. Ported from kormic-agents (github.py).
- **Proof of ownership** as its own record (`github_ownership`): the person's own token
  (used once, never stored) or a code in a public gist. The project decides what an
  unproved account means; the facts about the account are never lowered for it.
- Optional server token (`AGENTS_GITHUB_TOKEN`), shared daily limit, 24 h cache of public
  data (never of ownership or tokens). Outside calls go only to `api.github.com`.

## 1.2.6 — fixes from 7 more student CVs (October 2026)

- **CV (reader cv/5):** projects written as a title and bullets, with no date, are read
  from the document (they were all lost on 2 of 7 CVs); a bullet line is never a new
  entry (one CV's 4 roles came back as 12); a title that wraps onto a second line keeps
  its date; a section ends at its next heading ("Certifications & Achievements",
  "EXTRACURRICULARACTIVITIES"); when the document shows more entries plainly than the
  model's checked answer holds, they are read from the document.
- **CV checks:** an education entry needs a degree or a school ("(IT) 8" is dropped);
  `links` never holds an email or a phone number; `languages` never holds a programming
  language; "ABOUT ME" and spaced headings are never degrees.
- **PDF reading:** a text layer that puts each letter on its own line is replaced by the
  other one; text stored as "â€“" is repaired; icon-font bullets become "•"; spaced
  headings ("C O N T A C T") are read as words.
- **`agents try`** always prints UTF-8 (it crashed on Windows when the output went to a file).

## 1.2.5 — facts the small model lost, recovered from the document (October 2026)

The laptop's model (Qwen3 1.7B) often quotes the wrong line or invents a date, and the
checks rightly drop those answers. Where the document itself shows the fact plainly, it
is now read from the document. Every value is still a copy of the document's text.

- **CV (reader cv/4):** one "Work Experience / Projects" section is read (a
  "Title – Organisation" entry is a job, a title alone is a project); a date on its own
  line above a title belongs to that title; "Current CGPA" is not a date; school boards
  ("State Board") and "Secondary Education (10th Grade)" are education entries, with the
  dates kept off the title; a summary the model copied with its own punctuation keeps
  the CV's wording; a degree is never filed under other information.
- **LinkedIn (reader linkedin/4):** the location is quoted from its own line (it was
  dropped because the quote held the headline's "Intern"); a profile-card school keeps
  only the school when the model adds a made-up degree or dates; a skill quoted from the
  wrong line is quoted from its own; the mobile "top skills" page is read line by line.
- Checked by replaying the saved model answers from the laptop run: the CV now gives all
  3 education entries, the job and both projects; LinkedIn gives the school, location and
  all 5 skills.

## 1.2.4 — reading fixes from the CV and LinkedIn laptop retest (October 2026)

- **CV (reader cv/3):** a CV split into pieces is split at line starts. A piece used to
  start inside a word ("...ant Gajanan" from "Shri Sant Gajanan"), and the model read it as
  a new school, job and project.
- **LinkedIn (reader linkedin/3):** a phone's status bar read as the first line ("754 OOS
  ie" for 7:54) is dropped, and a block with almost no letters is never sent to the model.
  Before, it came back as a headline "OOS ie" and a location "OOS".
- **`AGENTS_DEBUG_DIR`** (test machines only): saves every model question and answer, to
  find why a reader drops a fact. Off by default; the worker warns when it is on.

## 1.2.3 — fixes from the first live SAM run (October 2026)

- **Notices of lookup agents have their own codes** (`not_a_clearance`, `listed_record`,
  `check_possible_match`, `mapping_unchecked`, ...). Before, every lookup notice had the
  CV code `excluded`, so a clean SAM screen could read as "excluded". Possible matches are
  `level: action`. Agents give codes with `Note(text, code, level, section)`.
- **Outside calls over IPv4** (`AGENTS_OUTBOUND_IPV4=true`): on an office network every
  SAM call waited 65 s for IPv6 to fail; over IPv4 it takes about 2 s.
- **SAM (reader sam/2):** timeout 60 s by default (SAM.gov's search is slow) and the whole
  answer may take twice that; dates as `YYYY-MM-DD`, and an end date in 2101 is `indefinite`.
- **All outside calls:** the 1.2.2 overall limit is now at least 120 s (it was twice the
  timeout, 30 s, and cut off slow SAM.gov answers).

## 1.2.2 — fixes from the review of 1.2.1 (October 2026)

- **CV PDF order:** the 1.2.1 reorder is now used only when a page stores its text out of
  order (a piece of text drawn between the lines of another piece in the same column).
  Pages in reading order, two-column CVs, tables and rotated pages read exactly as in 1.2.0.
  Text is grouped by character position, so close lines never swap letters.
- **LinkedIn:** the profile-card school is kept only on the card itself (just before
  "N connections") and never when it reads as an employer ("at", "@") or an issuer.
- **CV:** "Rank 3 of 10" is no longer read as a GPA scale.
- **SAM:** titles, suffixes, credentials (LCSW, MBA, ...) and middle names no longer hide
  a listing, and firms compare without their company type (LLC, INC). Before, "Jane Smith,
  LCSW" did not match "JANE ELIZABETH SMITH". A name of titles only is refused (422).
  An empty `AGENTS_SAM_PROJECT_DAILY_LIMIT=` means no project limit.
- **OIG and SAM:** "O'Brien" matches "OBRIEN" and "Abad-Santos" matches "ABADSANTOS"; OIG
  finds one-letter last names.
- **Service:** two services starting at once on PostgreSQL no longer migrate together;
  reader folders left by a crash are deleted after an hour; an outside source that sends
  bytes very slowly is cut off after twice its timeout.

## 1.2.1 — reading accuracy from the first laptop run (October 2026)

- **CV (reader cv/2):** PDF text is read in reading order by position. Some CV templates
  draw every heading first and every date last, so the model could not tell which lines
  were projects or which dates went with which entry. Runs at the same height now form one
  line (a date stays with its entry), lines go top to bottom, and a real sidebar column is
  read as its own column. Falls back to the stored order if the placed text loses anything.
- **CV:** `gpa_scale` is kept only when the CV writes a scale ("8.9/10", "out of 10",
  "10-point scale"); the model used to repeat the GPA when the CV gave no scale.
- **LinkedIn (reader linkedin/2):** mobile-app screenshots. The "<Name>'s top skills" page is
  recognised as a skills section (its skills were all rejected before); a page title such as
  "top skills" is never a headline; the school shown on the profile card is kept.

## 1.2.0 — lookup agents: NPPES, OIG, SAM (October 2026)

- Three lookup agents, ported from OneSmarter's kormic-agents package: **NPPES** (a US NPI
  at CMS), **OIG** (HHS-OIG exclusion list) and **SAM** (SAM.gov exclusions). Off by
  default; see [Lookup agents](lookup-agents.md).
- Changes from the original: NPPES no longer compares the given name with the record
  (projects decide; OIG and SAM still match names, only to search their lists); OIG no
  longer says an NPI-only hit matched on the name; SAM reads every page instead of the
  first 50 (also when SAM.gov gives no total) and refuses a partial answer; SAM compares
  each name given on its own (a former name could hide a hit) and never lets an inactive
  copy hide an active record; SAM's daily limit is shared by every worker and answers are
  cached under the exact name sent; the OIG list is loaded once and shared by every worker.
- Shared support for lookup agents: `lookups/http.py` (safe outside calls), `JobFailed`
  with new job error codes, agents' own settings (`AGENTS_<AGENT>_...`), a shared daily
  limit, a cache of outside answers and reference files (migration 0003),
  `agents tool <agent> <command>`, and each agent's state in `/health/ready`.
- httpx's own request logging (full addresses, so any key in them) is switched off.
- Second review: SAM sends only the first and last name words (SAM.gov ANDs every word, so
  a middle name or title could hide a listed person) and pages of 10 (GSA's maximum);
  an optional per-project SAM daily limit; NPPES treats an answer it cannot read as
  `source_unreadable`, never as "no such NPI"; OIG finds listings with only an initial
  or no first name and refuses names it cannot search ("Smith" alone); a corrected OIG
  publication date is picked up; `/health/ready` shows only ok / needs setup (the reason
  is in `GET /v1/agents`); `--data-file` reads UTF-8 (with or without BOM).

## 1.1.0 — fixes from the second review (October 2026)

- Hostile files stay out of the API: the API runs only quick checks (size, type, header,
  the Word file's part list). A PDF's page tree and a Word file's links are checked in the
  worker's limited reader process. A 90,000-page PDF now takes 0.0 s in the API (was 51 s).
- Each read has its own empty folder (working and temp folder), deleted afterwards; on
  timeout the reader and everything it started (tesseract) are killed together.
- Each agent's upload limit applies to its own address only.
- A database error while saving no longer marks a good result failed; the job returns to
  the line if it has attempts left. A retried job gets a fresh wait (new column `queued_at`,
  migration 0002; jobs from a 1.0 server during an upgrade use `created_at`).
- The sweep never deletes a waiting or running job. Results are kept 7 days after the job
  ends (was: after it was created). A job's input data is deleted when it ends, and the
  stored request fingerprint is keyed, so it cannot be used to guess that data.
- A relative `AGENTS_TESSERACT_CMD` is made absolute for the reader process.
- Fair turns count only the agents a worker serves. Running jobs check the line every 10 s.
- Job line settings that would break it are refused at start.
- Model overrides that change the provider or server get that provider's limits and never
  the global API key. `reasoning_effort` is sent only when set (Ollama keeps `none`).
- Lookup (data) agents: `empty_is_answer`, `data_record()`, `job.check_cancelled()`, a working
  template (`agents/_template_data`), `agents try --data`, and `data_schema` in `GET /v1/agents`.
- Agent definitions and record shapes are checked.
- Renamed to Universal Agents (package `universal-agents`).

## 1.0.0 — phase 1 (October 2026)

- New layered service `agents_service` with an agent registry; the October pilot moved to `legacy/`.
- Agents: CV (PDF, Word) and LinkedIn screenshots, using Kormic Careers' tested readers.
- Keys belong to one project and one agent; hashed, revocable, audited.
- One-call jobs at `/v1/<agent>/jobs` with `Idempotency-Key`, `fields` and `subject_ref`.
- Database job line: renewed borrows, fair turns between projects, expiry, retries; works with
  many workers on many machines.
- Storage on local disk or S3; SQLite or PostgreSQL; Alembic migrations.
- Model: Ollama/OpenAI-compatible (default) or Anthropic, per agent.
- Speed settings for CPU models: bigger CV pieces and text-first prompts.
- Results carry `reader_version`, `model` and `timing`; usage per project and agent (`agents stats`).
- `agents try`, `agents accuracy`, `agents check`; runs on Windows and Linux.
- Independent review fixes: a worker that lost its job never deletes the new holder's files;
  counters use single-statement upserts (safe on PostgreSQL); the worker survives save failures;
  Ctrl+C no longer kills a file being read; S3 cleanup touches only job folders; uploads are
  counted, not buffered, so keys are checked first; job checks have their own limit; the queue
  position follows fair turns; one request id per response; `AGENTS_FILE_SECONDS` bounds reading.
- The whole test suite also passes on PostgreSQL 16.
