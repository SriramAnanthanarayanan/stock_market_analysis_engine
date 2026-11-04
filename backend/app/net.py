"""Optional TLS trust configuration.

On some Windows machines Yahoo Finance / NSE requests fail with certificate
errors because Python's bundled CA list does not include certificates that the
OS trusts (corporate proxies, antivirus TLS inspection, ...).

``configure_ssl`` builds a combined CA bundle (certifi + Windows ROOT/CA
stores), points ``CURL_CA_BUNDLE``, ``SSL_CERT_FILE`` and
``REQUESTS_CA_BUNDLE`` at it (unless the user already set them), and injects
``truststore`` so the stdlib ``ssl`` module also consults the OS store.

It never disables certificate verification. Every step is best-effort: a
failure is logged and startup continues with the default SSL configuration.
Call it before importing yfinance.
"""

from __future__ import annotations

import logging
import os
import ssl
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"
_STATUS: dict[str, Any] = {"configured": False}


def _windows_store_pems() -> tuple[list[str], list[str]]:
    pems: list[str] = []
    errors: list[str] = []
    enum = getattr(ssl, "enum_certificates", None)
    if enum is None:
        return pems, errors
    for store in ("ROOT", "CA"):
        try:
            for cert, encoding, trust in enum(store):
                if encoding != "x509_asn":
                    continue
                if trust is True or (isinstance(trust, (set, frozenset)) and _SERVER_AUTH_OID in trust):
                    pems.append(ssl.DER_cert_to_PEM_cert(cert))
        except Exception as exc:  # pragma: no cover - platform specific
            errors.append(f"enum_certificates({store}): {exc}")
    return pems, errors


def configure_ssl(cache_dir: Path) -> dict[str, Any]:
    """Configure TLS trust once per process. Returns a status dict for /api/health."""
    if _STATUS.get("configured"):
        return dict(_STATUS)

    status: dict[str, Any] = {
        "configured": True,
        "bundle": None,
        "windows_certificates": 0,
        "truststore": False,
        "env_set": [],
        "errors": [],
    }

    parts: list[str] = []
    try:
        import certifi

        parts.append(Path(certifi.where()).read_text(encoding="utf-8"))
    except Exception as exc:
        status["errors"].append(f"certifi: {exc}")

    if sys.platform == "win32":
        pems, errors = _windows_store_pems()
        status["errors"].extend(errors)
        seen: set[str] = set()
        for pem in pems:
            if pem not in seen:
                seen.add(pem)
                parts.append(pem)
        status["windows_certificates"] = len(seen)

    if parts:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            bundle = cache_dir / "ca_bundle.pem"
            tmp = bundle.with_suffix(".pem.tmp")
            tmp.write_text("\n".join(parts), encoding="utf-8")
            os.replace(tmp, bundle)
            status["bundle"] = str(bundle)
            for var in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
                if os.environ.get(var):
                    log.info("SSL: %s already set by the environment; leaving it unchanged", var)
                    continue
                os.environ[var] = str(bundle)
                status["env_set"].append(var)
        except Exception as exc:
            status["errors"].append(f"bundle write: {exc}")

    try:
        import truststore

        truststore.inject_into_ssl()
        status["truststore"] = True
    except Exception as exc:
        status["errors"].append(f"truststore: {exc}")

    for err in status["errors"]:
        log.warning("SSL configuration step failed (continuing with defaults): %s", err)
    _STATUS.clear()
    _STATUS.update(status)
    return dict(status)


def ssl_status() -> dict[str, Any]:
    return dict(_STATUS)
