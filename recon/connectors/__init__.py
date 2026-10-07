"""Connector package. Importing it registers all built-in connectors."""

from recon.connectors import files, saas, sql  # noqa: F401  (registration side effects)
from recon.connectors.base import ConnectorError, available_types, describe, load, mask_url, register

__all__ = ["ConnectorError", "available_types", "describe", "load", "mask_url", "register"]
