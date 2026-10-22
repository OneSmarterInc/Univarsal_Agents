"""What every agent must define.

An agent is one folder in ``agents_service/agents/`` with an ``agent.py`` that
creates ``AGENT = SomeAgent()``. The API, keys, job line, limits, usage and
docs are built from these definitions, so adding an agent needs no change
anywhere else. See docs/adding-an-agent.md.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from agents_service.documents.safety import check_file

MB = 1024 * 1024


@dataclass(frozen=True)
class FileRule:
    """Which files an agent accepts."""

    # File ending -> accepted media types (application/octet-stream is always
    # accepted too; the content itself is checked, not the name).
    types: dict[str, tuple[str, ...]]
    min_files: int = 1
    max_files: int = 1
    max_bytes: int = 10 * MB


@dataclass
class InputFile:
    index: int
    name: str
    path: Path
    sha256: str


@dataclass
class JobContext:
    """Everything a running job may use."""

    job_id: str
    project: str
    files: list[InputFile]
    data: dict
    options: dict
    settings: object
    model: dict | None  # provider configuration (see agents_service.ai.providers)
    #: True once the job was cancelled (or taken over). Agents that call outside
    #: services call it between steps and stop with JobCancelled; model requests
    #: check it on their own.
    cancelled: Callable[[], bool] = lambda: False

    def check_cancelled(self):
        if self.cancelled():
            raise JobCancelled()


class JobCancelled(Exception):
    """The job was cancelled while it ran; nothing is saved."""


@dataclass
class AgentResult:
    records: list = field(default_factory=list)
    files: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Agent(ABC):
    #: Lower-case name, also the folder name and the address: /v1/<name>/jobs
    name: str = ''
    #: Short title and one-line description for GET /v1/agents and the docs.
    title: str = ''
    description: str = ''
    #: Key prefix, for example "cv" gives keys like ua_cv_...
    key_prefix: str = ''
    #: "files" (multipart upload) or "data" (a JSON body checked by data_model).
    input_kind: str = 'files'
    data_model: type[BaseModel] | None = None
    #: Sections the agent can return (records carry one of these).
    sections: tuple[str, ...] = ()
    #: Changes when the reader changes; returned with every result.
    reader_version: str = '1'
    #: False for agents that do not call a model at all.
    uses_model: bool = True
    #: True when "no records" is a normal answer (a lookup that found nothing),
    #: so the job ends "done". False for readers, where it means a failure.
    empty_is_answer: bool = False

    def file_rule(self, settings) -> FileRule | None:
        """The accepted files. Override when a limit comes from a setting."""
        return None

    def check_file(self, raw: bytes, filename: str, media_type: str, settings) -> str:
        """Safety-check one upload; returns the file ending to store it with."""
        return check_file(raw, filename, media_type, self.file_rule(settings))

    def describe(self, settings) -> dict:
        rule = self.file_rule(settings)
        info = {
            'name': self.name,
            'title': self.title,
            'description': self.description,
            'input': self.input_kind,
            'sections': list(self.sections),
            'reader_version': self.reader_version,
            'address': f'/v1/{self.name}/jobs',
            'empty_is_answer': self.empty_is_answer,
        }
        if self.input_kind == 'data' and self.data_model is not None:
            # What to send: POST {"data": {...}, "fields": "...", "subject_ref": "..."}
            info['data_schema'] = self.data_model.model_json_schema()
        if rule:
            info['files'] = {
                'types': sorted(rule.types),
                'min_files': rule.min_files,
                'max_files': rule.max_files,
                'max_bytes': rule.max_bytes,
            }
        return info

    @abstractmethod
    def run(self, job: JobContext) -> AgentResult:
        """Read the inputs and return the records found."""
