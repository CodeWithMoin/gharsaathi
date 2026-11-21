from app.core.config import Settings


def test_jev_reranking_is_opt_in() -> None:
    settings = Settings(_env_file=None)

    assert settings.jev_api_key.get_secret_value() == ""
    assert settings.jev_base_url == "https://api.typesafe.ai/v1"
    assert settings.jev_model == "jev-latest"
    assert settings.jev_reranking_enabled is False
