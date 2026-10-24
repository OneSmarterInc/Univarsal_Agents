"""One place that wires the parts together: settings, database, storage, job line, agents."""

from dataclasses import dataclass

from agents_service.agents import registry
from agents_service.core.config import Settings, get_settings
from agents_service.core.db import Database
from agents_service.core.jobs import JobLine
from agents_service.core.storage import make_storage


@dataclass
class Services:
    settings: Settings
    db: Database
    storage: object
    jobs: JobLine
    agents: dict


def build(settings=None):
    settings = settings or get_settings()
    db = Database(settings.database_url)
    storage = make_storage(settings)
    return Services(
        settings=settings,
        db=db,
        storage=storage,
        jobs=JobLine(db, storage, settings),
        agents=registry.enabled(settings),
    )
