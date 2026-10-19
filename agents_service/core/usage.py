"""Daily usage per project and agent, and the numbers behind `agents stats`."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from agents_service.core.models import Usage

COUNTERS = ('jobs', 'files', 'done', 'partial', 'failed', 'expired', 'run_seconds', 'wait_seconds')


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).strftime('%Y-%m-%d')


def add_usage(session, timestamp, project, agent, **amounts):
    """Add to today's counters for this project and agent (inside the caller's transaction)."""
    amounts = {k: v for k, v in amounts.items() if k in COUNTERS and v}
    if not amounts:
        return
    day = day_of(timestamp)
    key = (Usage.day == day) & (Usage.project == project) & (Usage.agent == agent)
    changed = session.execute(
        update(Usage)
        .where(key)
        .values({getattr(Usage, k): getattr(Usage, k) + v for k, v in amounts.items()})
    ).rowcount
    if not changed:
        row = Usage(day=day, project=project, agent=agent, **{k: 0 for k in COUNTERS})
        for name, value in amounts.items():
            setattr(row, name, value)
        session.add(row)
        session.flush()


def usage_report(db, days=7):
    """Totals per project and agent over the last `days` days."""
    since = (datetime.now(UTC) - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    totals = {}
    with db.session() as s:
        for row in s.scalars(select(Usage).where(Usage.day >= since)):
            entry = totals.setdefault((row.project, row.agent), {k: 0 for k in COUNTERS})
            for name in COUNTERS:
                entry[name] += getattr(row, name) or 0
    report = []
    for (project, agent), entry in sorted(totals.items()):
        finished = entry['done'] + entry['partial'] + entry['failed']
        report.append(
            {
                'project': project,
                'agent': agent,
                **{k: entry[k] for k in ('jobs', 'files', 'done', 'partial', 'failed', 'expired')},
                'average_run_seconds': round(entry['run_seconds'] / finished, 1) if finished else None,
                'average_wait_seconds': round(entry['wait_seconds'] / finished, 1) if finished else None,
            }
        )
    return {'since': since, 'generated_at': time.time(), 'rows': report}
