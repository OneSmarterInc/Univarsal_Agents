"""Daily usage per project and agent, and the numbers behind `agents stats`."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from agents_service.core.models import Usage

COUNTERS = ('jobs', 'files', 'done', 'partial', 'failed', 'expired', 'run_seconds', 'wait_seconds')


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).strftime('%Y-%m-%d')


def upsert(session, table, keys, amounts, fixed=None):
    """Insert a counter row or add `amounts` to it, in one statement (safe when many run at once).

    `fixed` values are set only when the row is created.
    """
    dialect = session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(table).values(**keys, **amounts, **(fixed or {}))
    columns = table.__table__.c
    statement = statement.on_conflict_do_update(
        index_elements=list(keys),
        set_={name: columns[name] + statement.excluded[name] for name in amounts},
    )
    session.execute(statement)


def add_usage(session, timestamp, project, agent, **amounts):
    """Add to today's counters for this project and agent (inside the caller's transaction)."""
    amounts = {k: v for k, v in amounts.items() if k in COUNTERS and v}
    if not amounts:
        return
    # Counters not given take their column default (0) on insert.
    upsert(session, Usage, {'day': day_of(timestamp), 'project': project, 'agent': agent}, amounts)


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
