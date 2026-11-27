"""Audience expansion -- the single send-time gate.

Every send path goes through `expand()`. That is deliberate: the opt-out check
lives here and nowhere else, so there is no way to add a new send path that
forgets it. Putting the check in each caller is how platforms end up texting
people who sent STOP, which is a compliance problem, not a bug report.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

# One query, one gate. Notes on the clauses:
#   - phone IS NOT NULL AND <> '' : a blank phone is a row that would cost a
#     provider call and fail, so it never reaches the queue.
#   - NOT EXISTS optout : opt-out is keyed by (church_id, phone), not person,
#     because the same handset can appear on several person rows and STOP has
#     to silence all of them.
#   - DISTINCT : a person in three of the selected groups is one recipient and
#     must be billed once.
_SQL = """
SELECT DISTINCT p.id AS person_id, p.phone
FROM person p
LEFT JOIN grp_member gm
       ON gm.person_id = p.id
      AND gm.church_id = p.church_id
WHERE p.church_id = $1
  AND p.phone IS NOT NULL
  AND p.phone <> ''
  AND (
        $4::boolean
     OR gm.group_id = ANY($2::bigint[])
     OR p.id        = ANY($3::bigint[])
  )
  AND NOT EXISTS (
        SELECT 1 FROM optout o
        WHERE o.church_id = p.church_id
          AND o.phone     = p.phone
  )
"""


async def expand(
    conn: "asyncpg.Connection", church_id: int, audience: list[dict]
) -> list:
    """Resolve an audience spec to deliverable (person_id, phone) rows.

    `audience` is a list of {kind: "group"|"person"|"all", id?: int}.
    Returns [] for an empty or unrecognised spec rather than silently selecting
    everyone -- defaulting to "all" on a malformed payload is how an accidental
    church-wide blast happens.
    """
    group_ids = [int(a["id"]) for a in audience if a.get("kind") == "group" and a.get("id")]
    person_ids = [int(a["id"]) for a in audience if a.get("kind") == "person" and a.get("id")]
    want_all = any(a.get("kind") == "all" for a in audience)

    if not (group_ids or person_ids or want_all):
        return []

    return await conn.fetch(_SQL, church_id, group_ids, person_ids, want_all)


def chunked(items: list, size: int):
    """Yield fixed-size chunks. Used to cut recipients into provider batches."""
    for i in range(0, len(items), size):
        yield items[i : i + size]
