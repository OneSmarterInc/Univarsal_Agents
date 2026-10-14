from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    type: Literal["cv", "linkedin", "github"]
    upload_ids: list[str] = Field(default_factory=list, max_length=10)
    connection_id: str | None = None
    repository_ids: list[StrictInt] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def check_inputs(self):
        if self.type == "github":
            if not self.connection_id or self.upload_ids or not self.repository_ids:
                raise ValueError("GitHub needs a connection and 1–5 repository IDs")
            if any(type(i) is not int or i <= 0 for i in self.repository_ids):
                raise ValueError("Invalid repository ID")
            if len(set(self.repository_ids)) != len(self.repository_ids):
                raise ValueError("Duplicate repository IDs")
        elif (
            self.connection_id
            or self.repository_ids
            or not self.upload_ids
            or (self.type == "cv" and len(self.upload_ids) != 1)
            or len(set(self.upload_ids)) != len(self.upload_ids)
        ):
            raise ValueError("Invalid upload selection")
        return self


class ExtractionRequest(Strict):
    schema_version: Literal["1.0"] = "1.0"
    subject_ref: str | None = Field(default=None, max_length=128)
    sources: list[Source] = Field(min_length=1, max_length=3)
    requested_fields: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_sources(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source ID")
        if len({s.type for s in self.sources}) != len(self.sources):
            raise ValueError("Only one source of each type per job")
        if set(self.requested_fields) - {s.id for s in self.sources}:
            raise ValueError("Unknown projection source")
        import re

        for fields in self.requested_fields.values():
            if len(fields) > 60 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", f) for f in fields):
                raise ValueError("Only top-level field projections are supported in v1")
        return self


class Claim(Strict):
    field: str = Field(max_length=64)
    value: str = Field(min_length=1, max_length=2000)
    block_id: str = Field(max_length=100)
    quote: str = Field(min_length=1, max_length=2000)


class Record(Strict):
    category: str = Field(max_length=64)
    claims: list[Claim] = Field(min_length=1, max_length=30)


class Observations(Strict):
    scalars: list[Claim] = Field(default_factory=list, max_length=100)
    records: list[Record] = Field(default_factory=list, max_length=40)


CV_SCALARS = {
    "name",
    "email",
    "phone",
    "location",
    "summary",
    "institution",
    "major",
    "graduation_year",
    "gpa",
    "gpa_scale",
}
CV_LISTS = {"links", "skills", "languages"}
CV_RECORDS = {
    key: {"title", "organization", "start_date", "end_date", "description", "url", "technologies"}
    for key in (
        "education",
        "work_experience_entries",
        "projects",
        "publications",
        "certifications",
        "awards",
        "volunteering",
        "other_information",
    )
}
LI_SCALARS = {"name", "headline", "about", "location"}
LI_LISTS = {"skills"}
LI_RECORDS = {
    "experiences": {"title", "company", "start_date", "end_date", "description"},
    "education": {"institution", "degree", "field_of_study", "start_date", "end_date"},
    "projects": {"name", "description", "technologies", "url"},
    "posts": {"text", "date", "topics"},
    "certifications": {"name", "issuer", "issue_date", "credential_id"},
}


def field_spec(kind):
    if kind == "cv":
        return CV_SCALARS, CV_LISTS, CV_RECORDS
    if kind == "linkedin":
        return LI_SCALARS, LI_LISTS, LI_RECORDS
    return {"summary"}, {"skills", "domains"}, {}
