from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    pass


def validate_outbound_url(
    url: str, *, allow_private: bool = False, require_https: bool = True
) -> str:
    """Проверить outbound url. Некорректные данные или состояние отклоняются до побочного эффекта."""
    parsed = urlparse(url)
    allowed_schemes = {"https"} if require_https else {"https", "http"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise UnsafeURL("URL должен содержать допустимую схему и hostname")
    if parsed.username or parsed.password:
        raise UnsafeURL("Credentials в URL запрещены")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        if not allow_private:
            raise UnsafeURL("Локальные адреса запрещены")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise UnsafeURL("Не удалось разрешить hostname") from exc
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not allow_private and (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeURL("URL разрешается в приватный или служебный IP")
    return url
