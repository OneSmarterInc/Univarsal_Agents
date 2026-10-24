"""The shapes the AI model must return for a CV."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Fact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str
    organization: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None
    evidence: str = Field(description='Exact supporting quotation from the resume')

    @field_validator('technologies', mode='before')
    @classmethod
    def empty_technologies(cls, value):
        return [] if value is None else value


class ResumeFacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    summary: str | None = None
    links: list[str] = Field(default_factory=list)
    institution: str | None = None
    major: str | None = None
    graduation_year: int | None = None
    gpa: float | None = None
    gpa_scale: str | None = None
    education: list[Fact] = Field(default_factory=list)
    work_experience_entries: list[Fact] = Field(default_factory=list)
    projects: list[Fact] = Field(default_factory=list)
    publications: list[Fact] = Field(default_factory=list)
    certifications: list[Fact] = Field(default_factory=list)
    awards: list[Fact] = Field(default_factory=list)
    volunteering: list[Fact] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    other_information: list[Fact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator(
        'links',
        'education',
        'work_experience_entries',
        'projects',
        'publications',
        'certifications',
        'awards',
        'volunteering',
        'skills',
        'languages',
        'other_information',
        'warnings',
        mode='before',
    )
    @classmethod
    def empty_collections(cls, value):
        # The provider prompt permits null for missing information. Preserve
        # strict validation of actual values, but accept absent collections.
        return [] if value is None else value
