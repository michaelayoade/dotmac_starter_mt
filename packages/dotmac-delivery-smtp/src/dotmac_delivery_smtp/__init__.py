"""SMTP delivery adapter for the kernel provider seam (channel `email`)."""

from dotmac_delivery_smtp.classify import classify_reply, enhanced_status
from dotmac_delivery_smtp.config import SmtpConfig, SmtpConfigError
from dotmac_delivery_smtp.provider import (
    AdmissionHook,
    AttachmentResolver,
    SmtpAdapterError,
    SmtpAttachmentError,
    SmtpAuthenticationError,
    SmtpDeliveryProvider,
    SmtpOutcomeUnknown,
)

__all__ = [
    "AdmissionHook",
    "AttachmentResolver",
    "SmtpAdapterError",
    "SmtpAttachmentError",
    "SmtpAuthenticationError",
    "SmtpConfig",
    "SmtpConfigError",
    "SmtpDeliveryProvider",
    "SmtpOutcomeUnknown",
    "classify_reply",
    "enhanced_status",
]
