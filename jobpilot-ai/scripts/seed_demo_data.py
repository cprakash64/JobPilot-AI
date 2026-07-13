import asyncio

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.entities import Experience, Project, User, UserProfile
from app.services.jobs import ingest_demo_jobs, match_jobs_for_user


async def main() -> None:
    db = SessionLocal()
    try:
        user = User(email="demo@example.com", hashed_password=hash_password("demo-password"))
        db.add(user)
        db.flush()
        db.add(
            UserProfile(
                user_id=user.id,
                full_name="Demo Student",
                location_city="Phoenix",
                location_state="AZ",
                location_country="United States",
                work_authorization="Authorized to work in the United States",
                requires_sponsorship=False,
                open_to_relocation=True,
                target_roles=["Software Engineer", "Backend Engineer"],
                target_levels=["new grad", "junior"],
                preferred_locations=["Remote", "Phoenix"],
                remote_preference="remote",
                skills=["Python", "FastAPI", "React", "TypeScript", "PostgreSQL", "Docker"],
            )
        )
        db.add(
            Experience(
                user_id=user.id,
                company="Campus Lab",
                title="Software Engineering Assistant",
                bullets=["Built internal dashboards for student support workflows."],
                technologies=["Python", "React", "PostgreSQL"],
                measurable_impact=["Reduced manual reporting time with reusable queries."],
            )
        )
        db.add(
            Project(
                user_id=user.id,
                name="Career Tracker",
                description="Full-stack app for tracking job applications.",
                bullets=["Implemented CRUD workflows and status reporting."],
                technologies=["FastAPI", "React", "Docker"],
                links=[],
            )
        )
        await ingest_demo_jobs(db)
        match_jobs_for_user(db, user.id)
        db.commit()
        print("Seeded demo@example.com / demo-password")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

