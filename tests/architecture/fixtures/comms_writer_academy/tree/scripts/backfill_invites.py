"""Synthetic fixture: a script family file that is already clean."""

from __future__ import annotations


def main(delivery, rows):
    for row in rows:
        delivery.send_email(
            template="invite", recipient=row.email, variables={"url": row.url}
        )
