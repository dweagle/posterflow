from core.config import _DEFAULT_CORS_ORIGINS, Settings


def test_cors_defaults_present_without_override():
    assert Settings().get_cors_origins() == list(_DEFAULT_CORS_ORIGINS)


def test_cors_extras_append_instead_of_replacing():
    origins = Settings(cors_origins=" https://example.com , http://localhost:5173 ").get_cors_origins()
    assert origins == [*_DEFAULT_CORS_ORIGINS, "https://example.com"]
