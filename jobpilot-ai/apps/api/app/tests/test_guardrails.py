from app.models.entities import JobPosting
from app.services.documents import build_truthful_resume, hallucination_check


def test_resume_does_not_add_unsupported_skills():
    payload = {
        "profile": {"full_name": "Demo", "skills": ["Python", "FastAPI"]},
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
        "awards": [],
    }
    job = JobPosting(
        external_id="1",
        title="Backend Engineer",
        company="DemoCo",
        application_url="https://example.com",
        source_url="https://example.com",
        description_raw="Python, Kubernetes",
        description_clean="Python, Kubernetes",
        required_skills=["Python", "Kubernetes"],
        preferred_skills=[],
        hash_for_deduplication="abc",
    )
    resume = build_truthful_resume(payload, job)
    assert "Kubernetes" not in resume["skills"]
    assert hallucination_check(resume, payload)["passed"]

