"""A record of who did what with which key. Never holds file content."""

import time

from agents_service.core.models import AuditEvent


def record(session, action, project=None, agent=None, key_id=None, job_id=None):
    session.add(
        AuditEvent(at=time.time(), action=action, project=project, agent=agent, key_id=key_id, job_id=job_id)
    )
