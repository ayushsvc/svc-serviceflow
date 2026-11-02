from app.core.config import Settings


def test_render_postgres_urls_are_normalized_for_async_sqlalchemy():
    settings = Settings(
        jwt_secret="test-secret-for-render-url-normalization",
        debug=True,
        database_url="postgres://svc:password@db.example.com:5432/service",
    )
    assert settings.database_url == "postgresql+asyncpg://svc:password@db.example.com:5432/service"

    settings = Settings(
        jwt_secret="test-secret-for-render-url-normalization",
        debug=True,
        database_url="postgresql://svc:password@db.example.com:5432/service",
    )
    assert settings.database_url == "postgresql+asyncpg://svc:password@db.example.com:5432/service"
