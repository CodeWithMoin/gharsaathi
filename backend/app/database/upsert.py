"""``INSERT … ON CONFLICT DO NOTHING`` for the dialects the app runs on."""

from typing import Any

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_if_absent(
    session: AsyncSession,
    model: type[Any],
    values: dict[str, Any],
    conflict_columns: list[str],
) -> None:
    """Insert one row unless it would violate the unique key on ``conflict_columns``.

    PostgreSQL makes a concurrent inserter wait for the first transaction and
    then skip, so a following SELECT always finds exactly one committed row.
    """

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql.insert(model)
    elif dialect == "sqlite":
        statement = sqlite.insert(model)
    else:  # pragma: no cover - the app only targets these two dialects
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    await session.execute(
        statement.values(**values).on_conflict_do_nothing(index_elements=conflict_columns)
    )
