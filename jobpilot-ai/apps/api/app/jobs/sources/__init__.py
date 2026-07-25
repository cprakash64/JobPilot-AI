"""Public/allowed job source adapters.

Only compliant sources are exposed here: public ATS job-board APIs
(Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, Teamtailor,
Breezy) plus SimplifyJobs' published GitHub feeds. No LinkedIn/Indeed/Glassdoor
or Lensa scraping, no browser automation, and no proxy rotation.
"""

from app.job_sources.greenhouse import GreenhouseAdapter
from app.job_sources.lever import LeverAdapter
from app.jobs.sources.ashby import AshbyAdapter
from app.jobs.sources.breezy import BreezyAdapter
from app.jobs.sources.recruitee import RecruiteeAdapter
from app.jobs.sources.simplifyjobs import SimplifyJobsAdapter
from app.jobs.sources.smartrecruiters import SmartRecruitersAdapter
from app.jobs.sources.teamtailor import TeamtailorAdapter
from app.jobs.sources.workable import WorkableAdapter

ADAPTERS = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "recruitee": RecruiteeAdapter,
    "workable": WorkableAdapter,
    "teamtailor": TeamtailorAdapter,
    "breezy": BreezyAdapter,
    "simplifyjobs": SimplifyJobsAdapter,
}

# The key each provider uses to name its board/company slug inside the catalog.
SLUG_KEYS = {
    "greenhouse": "board_token",
    "lever": "site",
    "ashby": "board",
    "smartrecruiters": "company_id",
    "recruitee": "company",
    "workable": "account",
    "teamtailor": "account",
    "breezy": "company",
    "simplifyjobs": "repository",
}

__all__ = [
    "GreenhouseAdapter", "LeverAdapter", "AshbyAdapter", "SmartRecruitersAdapter",
    "RecruiteeAdapter", "WorkableAdapter", "TeamtailorAdapter", "BreezyAdapter",
    "SimplifyJobsAdapter",
    "ADAPTERS", "SLUG_KEYS",
]
