"""SMTP delivery adapter for the kernel provider seam (channel `email`)."""

from dotmac_delivery_smtp.classify import classify_reply, enhanced_status
from dotmac_delivery_smtp.config import SmtpConfig, SmtpConfigError
from dotmac_delivery_smtp.provider import (
    AdmissionHook,
    AttachmentResolver,
    SmtpAdapterError,
    SmtpAdmissionRefused,
    SmtpAttachmentError,
    SmtpAuthenticationError,
    SmtpDeliveryProvider,
    SmtpOperatorFault,
    SmtpOutcomeUnknown,
)
from dotmac_delivery_smtp.reader import resolve_config

__all__ = [
    "AdmissionHook",
    "AttachmentResolver",
    "SmtpAdapterError",
    "SmtpAdmissionRefused",
    "SmtpOperatorFault",
    "SmtpAttachmentError",
    "SmtpAuthenticationError",
    "SmtpConfig",
    "SmtpConfigError",
    "SmtpDeliveryProvider",
    "SmtpOutcomeUnknown",
    "classify_reply",
    "resolve_config",
    "enhanced_status",
]
