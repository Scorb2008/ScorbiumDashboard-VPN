from fix_alembic import _determine_target_version


def test_determine_target_version_detects_current_head():
    schema = {
        "has_language": True,
        "has_autorenew": True,
        "has_payment_type": True,
        "has_admins": True,
        "has_performance_indexes": True,
        "has_admin_features": True,
        "has_admin_totp": True,
        "has_admin_backup_codes": True,
        "has_vpn_traffic": True,
        "has_blacklisted_tokens": True,
        "has_promo_usages": True,
        "has_branding_assets": True,
    }

    assert _determine_target_version(schema) == "e8f9a0b1c2d3"


def test_determine_target_version_keeps_old_schema_upgradeable():
    schema = {
        "has_language": True,
        "has_autorenew": True,
        "has_payment_type": True,
        "has_admins": False,
    }

    assert _determine_target_version(schema) == "c4d5e6f7a8b9"


def test_determine_target_version_handles_mid_chain_restore():
    schema = {
        "has_language": True,
        "has_autorenew": True,
        "has_payment_type": True,
        "has_admins": True,
        "has_performance_indexes": True,
        "has_admin_features": True,
        "has_admin_totp": True,
        "has_admin_backup_codes": True,
        "has_vpn_traffic": True,
        "has_blacklisted_tokens": True,
        "has_promo_usages": False,
        "has_branding_assets": False,
    }

    assert _determine_target_version(schema) == "c6d7e8f9a0b1"
