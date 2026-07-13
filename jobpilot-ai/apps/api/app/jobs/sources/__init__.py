"""Public/allowed job source adapters.

Only compliant sources are exposed here: public ATS job-board APIs
(Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, Teamtailor,
Breezy). No LinkedIn/Indeed/Glassdoor scraping, no browser automation, no proxy
rotation. Workable/Teamtailor use official APIs and are config-gated for tokens.
"""

from app.job_sources.greenhouse import GreenhouseAdapter
from app.job_sources.lever import LeverAdapter
from app.jobs.sources.ashby import AshbyAdapter
from app.jobs.sources.breezy import BreezyAdapter
from app.jobs.sources.recruitee import RecruiteeAdapter
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
}

__all__ = [
    "GreenhouseAdapter", "LeverAdapter", "AshbyAdapter", "SmartRecruitersAdapter",
    "RecruiteeAdapter", "WorkableAdapter", "TeamtailorAdapter", "BreezyAdapter",
    "ADAPTERS", "SLUG_KEYS",
]
