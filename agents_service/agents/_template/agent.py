"""Template for a new agent. Copy this folder, rename it, and follow docs/adding-an-agent.md.

As written it is a working example: it reads text documents (PDF, Word) and
returns one record per non-empty line that starts with "Key:" style labels,
without calling a model. Replace `run` with the real reading.
"""

import re

from agents_service.agents.base import MB, Agent, AgentResult, FileRule, JobContext
from agents_service.agents.records import add_records, read_files, stage
from agents_service.documents.isolation import read_text
from agents_service.documents.reading import ExtractionError

LABEL = re.compile(r'^([A-Za-z][A-Za-z ]{1,30}):\s*(.+)$')


class TemplateAgent(Agent):
    # 1. Name = folder name = address (/v1/<name>/jobs). Lower case, no spaces.
    name = 'template'
    title = 'Template agent'
    description = 'Example agent: returns "Label: value" lines from a document.'
    # 2. Key prefix: keys look like ua_<prefix>_...
    key_prefix = 'tpl'
    # 3. "files" for uploads, or "data" with a pydantic data_model for JSON input.
    input_kind = 'files'
    # 4. Every section a record can have.
    sections = ('labelled_value',)
    # 5. Change this whenever the reading changes; it is returned with every result.
    reader_version = 'template/1'
    # 6. True if run() calls a model (the worker then passes job.model).
    uses_model = False

    def file_rule(self, settings):
        return FileRule(
            types={
                '.pdf': ('application/pdf',),
                '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',),
            },
            max_files=3,
            max_bytes=10 * MB,
        )

    def run(self, job: JobContext) -> AgentResult:
        def read_one(file, records, warnings):
            with stage('read'):
                text = read_text(file.path, job.settings)
            with stage('validate'):
                found = []
                for line in text.splitlines():
                    match = LABEL.match(line.strip())
                    if match:
                        # Every record keeps the exact quote it came from.
                        found.append(
                            {'label': match.group(1), 'value': match.group(2), 'evidence': line.strip()}
                        )
                count = add_records(records, {'labelled_value': found}, file)
            if not count:
                raise ExtractionError('No "Label: value" lines were found.')
            return count

        records, files, warnings = read_files(job.files, read_one)
        return AgentResult(records=records, files=files, warnings=warnings)


AGENT = TemplateAgent()
