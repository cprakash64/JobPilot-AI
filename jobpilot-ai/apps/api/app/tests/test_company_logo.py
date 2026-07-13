"""Company logo resolution: curated map, explicit catalog, and safe fallback."""

from app.jobs.company_logo_service import logo_url_for_domain, resolve_company_logo


def test_resolves_known_company_from_curated_map():
    for company, domain in [
        ("OpenAI", "openai.com"),
        ("Deepgram", "deepgram.com"),
        ("Plaid", "plaid.com"),
        ("Temporal", "temporal.io"),
    ]:
        result = resolve_company_logo(company)
        assert result["company_domain"] == domain
        assert domain in result["company_logo_url"]
        assert result["confidence"] in {"high", "medium"}


def test_logo_url_uses_reliable_favicon_endpoint_not_clearbit():
    url = logo_url_for_domain("openai.com")
    assert "clearbit" not in url  # the sunset API that caused blank logos
    assert url.startswith("https://")
    assert "openai.com" in url


def test_normalizes_company_suffixes():
    result = resolve_company_logo("Plaid, Inc.")
    assert result["company_domain"] == "plaid.com"


def test_explicit_catalog_domain_wins():
    result = resolve_company_logo("Acme Corp", catalog_domain="acme.dev")
    assert result["company_domain"] == "acme.dev"
    assert "acme.dev" in result["company_logo_url"]
    assert result["confidence"] == "high"


def test_explicit_catalog_logo_url_used_verbatim():
    result = resolve_company_logo("Acme", catalog_logo_url="https://cdn.example.com/acme.png")
    assert result["company_logo_url"] == "https://cdn.example.com/acme.png"


def test_unknown_company_falls_back_to_empty():
    result = resolve_company_logo("Totally Unknown Startup XYZ")
    assert result["company_domain"] == ""
    assert result["company_logo_url"] == ""
    assert result["confidence"] == "low"


def test_never_guesses_domain_from_application_url_slug():
    # An ATS slug must not be turned into a random domain.
    result = resolve_company_logo(
        "Totally Unknown Startup XYZ",
        application_url="https://boards.greenhouse.io/acme/123",
    )
    assert result["company_logo_url"] == ""


def test_bad_catalog_domain_is_ignored():
    result = resolve_company_logo("Totally Unknown Startup XYZ", catalog_domain="not a domain")
    assert result["company_domain"] == ""
    assert result["company_logo_url"] == ""
