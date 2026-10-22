"""Template for a lookup (data) agent: JSON in, records from an outside source out.

Copy this folder for an agent that looks something up (a register, a list)
instead of reading uploaded files, and follow docs/adding-an-agent.md.

As written it is a working example with no outside call: it "looks up" the
given number in a small built-in register. Replace `lookup` with the real call
(httpx, with a timeout), keep `job.check_cancelled()` between calls.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from agents_service.agents.base import Agent, AgentResult, JobContext
from agents_service.agents.records import data_record

SOURCE = 'https://register.example/lookup'
EXAMPLE_REGISTER = {'1234567893': {'name': 'Alex Example', 'status': 'active'}}


class LookupInput(BaseModel):
    """What a project sends in "data". Unknown fields are refused."""

    model_config = ConfigDict(extra='forbid')
    number: str = Field(min_length=1, max_length=40)
    full_name: str | None = Field(default=None, max_length=200)


class TemplateLookupAgent(Agent):
    name = 'template_lookup'
    title = 'Template lookup agent'
    description = 'Example lookup agent: finds a number in a small built-in register.'
    key_prefix = 'tpldata'
    # JSON input, checked by data_model before the job is queued.
    input_kind = 'data'
    data_model = LookupInput
    sections = ('register_entry', 'not_found')
    reader_version = 'template_lookup/1'
    uses_model = False
    # A lookup that finds nothing is an answer, not a failure.
    empty_is_answer = True

    def run(self, job: JobContext) -> AgentResult:
        number = job.data['number']
        job.check_cancelled()  # call between outside requests: a cancelled job stops here
        entry, fetched = lookup(number)
        if entry is None:
            # Say plainly that nothing was found, so the project can tell it from an error.
            return AgentResult(records=[data_record('not_found', {'number': number}, SOURCE, fetched)])
        return AgentResult(
            records=[data_record('register_entry', {'number': number, **entry}, SOURCE, fetched)]
        )


def lookup(number):
    """(entry or None, when it was fetched). The real agent calls the outside source here."""
    return EXAMPLE_REGISTER.get(number), datetime.now(UTC).isoformat()


AGENT = TemplateLookupAgent()
