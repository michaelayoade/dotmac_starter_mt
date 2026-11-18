"""Synthetic fixture: a product-owned mail queue table."""

from __future__ import annotations


class EmailOutbox:
    __tablename__ = "email_outbox"
    id = None
    subject = None
    html_body = None
