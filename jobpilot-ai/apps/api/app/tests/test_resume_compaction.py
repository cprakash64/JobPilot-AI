"""Unit tests for the resume compaction / de-duplication service."""

from app.documents.resume_compaction_service import (
    MAX_BULLETS_PER_EXPERIENCE,
    MAX_EXPERIENCE,
    MAX_PROJECTS,
    compact_resume,
    dedupe_education,
    dedupe_projects,
    group_skills,
    looks_like_fragment,
    select_relevant_projects,
)


def _working(**over):
    base = {
        "header": {"name": "A"},
        "summary": "s",
        "skills": [],
        "experience": [],
        "projects": [],
        "education": [],
        "awards": [],
        "certifications": [],
    }
    base.update(over)
    return base


def test_dedupes_duplicate_experience():
    exp = {"company": "Cardinal Health", "title": "ML Engineer Intern", "start_date": "2024", "end_date": "2025", "bullets": ["Built services"]}
    working = _working(experience=[exp, dict(exp), {"company": "cardinal health", "title": "ml engineer intern", "start_date": "2024", "end_date": "2025", "bullets": ["Built services"]}])
    out = compact_resume(working, set())
    assert len(out["experience"]) == 1


def test_dedupes_duplicate_education_arizona_state():
    working = _working(
        education=[
            {"school": "Arizona State University", "degree": "B.S.", "major": "Computer Science", "gpa": "3.8"},
            {"school": "Arizona State University", "degree": "Bachelor of Science", "major": "Computer Science"},
            {"school": "ASU", "degree": "", "major": ""},  # sparse variant / alias
        ]
    )
    out = compact_resume(working, set())
    schools = [e["school"] for e in out["education"]]
    assert schools == ["Arizona State University"]
    # The most complete record wins (keeps GPA).
    assert out["education"][0].get("gpa") == "3.8"


def test_dedupe_education_keeps_distinct_degrees():
    edu = dedupe_education([
        {"school": "Arizona State University", "degree": "BS", "major": "CS"},
        {"school": "Arizona State University", "degree": "MS", "major": "CS"},
    ])
    assert len(edu) == 2


def test_dedupe_education_fills_blank_fields_from_duplicate():
    edu = dedupe_education([
        {"school": "MIT", "degree": "BS", "major": "", "gpa": ""},
        {"school": "MIT", "degree": "", "major": "EECS", "gpa": "3.9"},
    ])
    assert len(edu) == 1
    assert edu[0]["major"] == "EECS"
    assert edu[0]["gpa"] == "3.9"


def test_select_relevant_projects_included_and_ranked():
    projects = [
        {"name": "Spreadsheet Macro", "technologies": ["Excel"], "bullets": ["Automated a sheet"]},
        {"name": "Luna AI", "technologies": ["Python", "OpenAI"], "bullets": ["Built RAG pipeline with FastAPI"]},
        {"name": "VeoTrex", "technologies": ["PyTorch", "OpenCV"], "bullets": ["Trained CV models"]},
    ]
    selected = select_relevant_projects(projects, {"python", "pytorch", "rag", "fastapi", "backend"}, max_projects=2)
    names = [p["name"] for p in selected]
    assert len(selected) == 2
    assert "Luna AI" in names or "VeoTrex" in names
    assert "Spreadsheet Macro" not in names


def test_select_relevant_projects_keeps_one_for_junior_when_bullets_weak():
    # Even with a weak/empty bullet, a real project must survive for junior resumes.
    projects = [{"name": "LunaiCAD", "technologies": ["Python"], "bullets": []}]
    selected = select_relevant_projects(projects, {"python", "backend"}, max_projects=2)
    assert [p["name"] for p in selected] == ["LunaiCAD"]


def test_dedupe_projects_removes_duplicates():
    out = dedupe_projects([{"name": "Luna AI"}, {"name": "luna ai"}, {"name": "VeoTrex"}])
    assert [p["name"] for p in out] == ["Luna AI", "VeoTrex"]


def test_compact_resume_keeps_projects_present():
    working = _working(projects=[
        {"name": "Luna AI", "technologies": ["Python"], "bullets": ["Built RAG pipeline", "Shipped API"]},
    ])
    out = compact_resume(working, {"python"}, job_keywords={"python", "backend"})
    assert len(out["projects"]) == 1
    assert out["projects"][0]["name"] == "Luna AI"
    assert len(out["projects"][0]["bullets"]) <= 2


def test_dedupes_duplicate_projects_and_drops_fragments():
    working = _working(
        projects=[
            {"name": "Luna AI", "technologies": ["Python"], "bullets": ["Built pipeline"]},
            {"name": "luna ai", "technologies": ["Python"], "bullets": ["Built pipeline"]},  # dup
            {"name": "validation states.", "bullets": ["x"]},  # fragment
            {"name": "timestamped video results.", "bullets": ["y"]},  # fragment
        ]
    )
    out = compact_resume(working, set())
    names = [p["name"] for p in out["projects"]]
    assert names == ["Luna AI"]


def test_dedupes_duplicate_awards():
    working = _working(awards=[{"name": "Dean's List"}, {"name": "Dean's List"}, {"name": "DEAN'S LIST"}])
    out = compact_resume(working, set())
    assert len(out["awards"]) == 1


def test_dedupes_bullets_and_removes_empty():
    exp = {"company": "C", "title": "T", "bullets": ["Built API", "Built API", "  ", "-", "Improved latency"]}
    out = compact_resume(_working(experience=[exp]), set())
    bullets = out["experience"][0]["bullets"]
    assert bullets == ["Built API", "Improved latency"]


def test_caps_experience_and_bullets_for_one_page():
    experiences = [
        {"company": f"Co{i}", "title": f"Role{i}", "bullets": [f"b{i}-{j}" for j in range(8)]}
        for i in range(6)
    ]
    out = compact_resume(_working(experience=experiences), set())
    assert len(out["experience"]) == MAX_EXPERIENCE
    for exp in out["experience"]:
        assert len(exp["bullets"]) <= MAX_BULLETS_PER_EXPERIENCE


def test_projects_capped_and_ranked_by_job_relevance():
    working = _working(
        projects=[
            {"name": "Irrelevant", "technologies": ["Excel"], "bullets": ["Made a sheet"]},
            {"name": "MLPipe", "technologies": ["PyTorch"], "bullets": ["Trained models"]},
            {"name": "RagBot", "technologies": ["RAG", "Python"], "bullets": ["Built RAG"]},
        ]
    )
    out = compact_resume(working, {"pytorch", "rag", "python"})
    assert len(out["projects"]) == MAX_PROJECTS
    assert out["projects"][0]["name"] in {"RagBot", "MLPipe"}
    assert "Irrelevant" not in [p["name"] for p in out["projects"]]


def test_looks_like_fragment():
    assert looks_like_fragment("validation states.")
    assert looks_like_fragment("timestamped video results.")
    assert looks_like_fragment("a b c d e f g h i")  # too long
    assert not looks_like_fragment("LunaiCAD")
    assert not looks_like_fragment("Luna AI")
    assert not looks_like_fragment("VeoTrex")


def test_group_skills_creates_categories():
    skills = ["Python", "PyTorch", "FastAPI", "PostgreSQL", "Docker", "TypeScript", "GitHub Actions"]
    groups = group_skills(skills, set())
    labels = {g["category"] for g in groups}
    assert "ML / AI" in labels
    assert "Backend / Cloud" in labels
    assert "Programming" in labels
    # No giant single comma list.
    assert len(groups) >= 3


def test_group_skills_puts_job_relevant_category_first():
    skills = ["Excel", "Python", "PyTorch"]
    groups = group_skills(skills, {"pytorch"})
    assert any(item.lower() == "pytorch" for item in groups[0]["items"])


def test_group_skills_does_not_invent_skills():
    groups = group_skills(["Python"], {"kubernetes"})
    all_items = [item for g in groups for item in g["items"]]
    assert all_items == ["Python"]
