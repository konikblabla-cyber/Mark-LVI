"""Network exposure policy for the optional phone dashboard."""


def dashboard_https_requested(config) -> bool:
    """Default to TLS; malformed or unreadable config is handled by caller."""
    if not isinstance(config, dict):
        return False
    return bool(config.get("dashboard_https", True))


def dashboard_bind_host(https_enabled: bool) -> str:
    """Only a TLS-protected dashboard may listen on the LAN."""
    return "0.0.0.0" if https_enabled else "127.0.0.1"


def dashboard_display_host(https_enabled: bool, lan_ip: str) -> str:
    return lan_ip if https_enabled else "127.0.0.1"
