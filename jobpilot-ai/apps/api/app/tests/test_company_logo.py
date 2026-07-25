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


def test_direct_employer_application_domain_can_supply_branding():
    result = resolve_company_logo(
        "Example Robotics",
        application_url="https://careers.example-robotics.com/jobs/123",
    )
    assert result["company_domain"] == "example-robotics.com"
    assert "example-robotics.com" in result["company_logo_url"]


def test_shared_workday_domain_is_never_used_as_the_company_logo():
    result = resolve_company_logo(
        "Totally Unknown Startup XYZ",
        application_url="https://unknown.wd5.myworkdayjobs.com/en-US/jobs/job/1",
    )
    assert result["company_domain"] == ""
    assert result["company_logo_url"] == ""


def test_simplify_feed_employers_have_curated_branding():
    for company, domain in [
        ("KAYAK", "kayak.com"),
        ("Globus Medical", "globusmedical.com"),
        ("The Boeing Company", "boeing.com"),
        ("RTX", "rtx.com"),
        ("T. Rowe Price", "troweprice.com"),
    ]:
        assert resolve_company_logo(company)["company_domain"] == domain


def test_shared_smartrecruiters_employers_have_curated_branding():
    for company, domain in [
        ("Bosch", "bosch.com"),
        ("ServiceNow", "servicenow.com"),
        ("Western Digital", "westerndigital.com"),
        ("Turner & Townsend", "turnerandtownsend.com"),
    ]:
        assert resolve_company_logo(company)["company_domain"] == domain


def test_pogo_technologies_suffixes_normalize_to_verified_domain():
    assert (
        resolve_company_logo("Pogo Technologies, Inc.")["company_domain"]
        == "joinpogo.com"
    )


def test_simplify_mirror_never_overrides_verified_company_domain():
    result = resolve_company_logo(
        "Southwest Airlines",
        source_type="simplifyjobs",
        catalog_domain="southwest.com",
        catalog_logo_url=(
            "https://storage.googleapis.com/simplify-imgs/companies/"
            "e5c6c6ef-6057-4736-ba66-a7697ccee04a/logo.png"
        ),
    )
    assert result["company_domain"] == "southwest.com"
    assert "southwest.com" in result["company_logo_url"]
    assert "simplify-imgs" not in result["company_logo_url"]


def test_crackajack_uses_verified_first_party_logo_asset():
    result = resolve_company_logo("CrackaJack Digital Solutions LLC")
    assert result["company_domain"] == "crackajackllc.com"
    assert result["company_logo_url"].startswith("https://www.crackajackllc.com/")
