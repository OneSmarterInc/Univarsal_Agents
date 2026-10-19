"""The shapes the AI model must return for one LinkedIn screenshot."""

from typing import Any

from pydantic import BaseModel, Field, field_validator


def _list_or_empty(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None]
    return [value]


class EvidenceValue(BaseModel):
    value: str | None = None
    evidence: str = ''


class SkillObservation(BaseModel):
    name: str | None = None
    evidence: str = ''


class ExperienceObservation(BaseModel):
    title: str | None = None
    company: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    evidence: str = ''


class EducationObservation(BaseModel):
    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    evidence: str = ''


class ProjectObservation(BaseModel):
    name: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None
    evidence: str = ''

    @field_validator('technologies', mode='before')
    @classmethod
    def normalize_technologies(cls, value):
        return _list_or_empty(value)


class PostObservation(BaseModel):
    text: str | None = None
    date: str | None = None
    topics: list[str] = Field(default_factory=list)
    evidence: str = ''

    @field_validator('topics', mode='before')
    @classmethod
    def normalize_topics(cls, value):
        return _list_or_empty(value)


class CertificationObservation(BaseModel):
    name: str | None = None
    issuer: str | None = None
    issue_date: str | None = None
    credential_id: str | None = None
    evidence: str = ''


class ImageObservation(BaseModel):
    name: EvidenceValue | None = Field(
        default=None, description='Owner name from profile header; null if absent.'
    )
    headline: EvidenceValue | None = Field(
        default=None, description='Full profile headline, not employment history.'
    )
    about: EvidenceValue | None = Field(default=None, description='About text; include heading in evidence.')
    location: EvidenceValue | None = Field(default=None, description='Explicit profile location.')
    skills: list[SkillObservation] = Field(default_factory=list)
    experiences: list[ExperienceObservation] = Field(default_factory=list)
    education: list[EducationObservation] = Field(default_factory=list)
    projects: list[ProjectObservation] = Field(default_factory=list)
    posts: list[PostObservation] = Field(default_factory=list)
    certifications: list[CertificationObservation] = Field(default_factory=list)

    @field_validator(
        'skills',
        'experiences',
        'education',
        'projects',
        'posts',
        'certifications',
        mode='before',
    )
    @classmethod
    def normalize_lists(cls, value):
        return _list_or_empty(value)

    @classmethod
    def empty(cls):
        return cls()
