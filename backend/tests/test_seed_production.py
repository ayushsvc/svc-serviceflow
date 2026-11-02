import pytest

from app.seed import _configured_users


def test_production_seed_configures_only_the_supplied_admin(monkeypatch):
    monkeypatch.setenv("SEED_ADMIN_EMAIL", "Initial.Admin@example.com")
    monkeypatch.setenv("SEED_ADMIN_NAME", "Initial Administrator")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "long-unique-bootstrap-password")
    monkeypatch.delenv("SEED_TECHNICIAN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_REQUESTER_PASSWORD", raising=False)

    users = _configured_users(production=True)

    assert users == [
        (
            "initial.admin@example.com",
            "Initial Administrator",
            "Administrator",
            "long-unique-bootstrap-password",
        )
    ]


@pytest.mark.parametrize(
    "email,name,password",
    [
        ("not-an-email", "Admin", "long-unique-bootstrap-password"),
        ("admin@example.com", "", "long-unique-bootstrap-password"),
        ("admin@example.com", "Admin", "short"),
        ("admin@example.com", "Admin", "x" * 73),
    ],
)
def test_production_seed_rejects_invalid_bootstrap_admin(monkeypatch, email, name, password):
    monkeypatch.setenv("SEED_ADMIN_EMAIL", email)
    monkeypatch.setenv("SEED_ADMIN_NAME", name)
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", password)

    with pytest.raises(RuntimeError):
        _configured_users(production=True)
