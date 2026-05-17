"""Role-based permission system for the admin panel."""

PERMISSIONS: dict[str, set[str]] = {
    "superadmin": {"*"},
    "manager": {
        "dashboard",
        "users",
        "users.read",
        "users.write",
        "payments",
        "subscriptions",
        "plans",
        "promos",
        "referrals",
        "broadcasts",
        "export",
        "vpn",
        "system",
        "monitoring",
    },
    "operator": {
        "dashboard",
        "support",
        "users.read",
        "subscriptions.read",
        "system",
        "monitoring",
    },
}


def has_permission(role: str, permission: str) -> bool:
    """Check if a role has a specific permission.

    Supports wildcard ("*") and parent-grants-child logic:
    having "users" grants "users.read", "users.write", etc.
    """
    normalized_role = (role or "").strip().lower()
    normalized_permission = (permission or "").strip()

    perms = PERMISSIONS.get(normalized_role, set())
    if "*" in perms:
        return True
    if normalized_permission in perms:
        return True

    # Check parent permission (e.g. "users" grants "users.read")
    parent = normalized_permission.rsplit(".", 1)[0]
    if parent != normalized_permission and parent in perms:
        return True
    return False
