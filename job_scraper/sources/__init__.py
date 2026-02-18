"""Job source adapters – one module per external API / feed."""

from .remoteok import RemoteOKSource
from .arbeitnow import ArbeitnowSource
from .themuse import TheMuseSource
from .jobicy import JobicySource
from .remotive import RemotiveSource
from .weworkremotely import WeWorkRemotelySource
from .adzuna import AdzunaSource
from .reed import ReedSource
from .usajobs import USAJobsSource
from .jooble import JoobleSource
from .serpapi_google import SerpAPIGoogleJobsSource
from .findwork import FindworkSource
from .jobspy_source import JobSpySource
from .linkedin import LinkedInSource
from .linkedin_direct import LinkedInDirectSource
from .workingnomads import WorkingNomadsSource
from .lobsters import LobstersSource
from .careerjet import CareerJetSource
from .greenhouse import GreenhouseSource
from .lever import LeverSource
from .ashby import AshbySource
from .workable import WorkableSource
from .jobscollider import JobsColliderSource
from .devitjobs import DevITJobsSource
from .hn_hiring import HackerNewsHiringSource
from .totaljobs import TotaljobsSource
from .remote_co import RemoteCoSource
from .govuk_findajob import GovUKFindAJobSource
from .jobdata import JobDataSource

# Registry: name → class.  Sources with API key requirements are included
# but will gracefully skip if keys are not configured.
ALL_SOURCES = {
    # ── Free (no key needed) ──────────────────────────────────
    "RemoteOK": RemoteOKSource,
    "Arbeitnow": ArbeitnowSource,
    "The Muse": TheMuseSource,
    "Jobicy": JobicySource,
    "Remotive": RemotiveSource,
    "WeWorkRemotely": WeWorkRemotelySource,
    "WorkingNomads": WorkingNomadsSource,
    "Lobsters": LobstersSource,
    "Greenhouse": GreenhouseSource,
    "Lever": LeverSource,
    "Ashby": AshbySource,
    "Workable": WorkableSource,
    "JobsCollider": JobsColliderSource,
    "DevITjobs": DevITJobsSource,
    "HN Who is hiring": HackerNewsHiringSource,
    "Totaljobs": TotaljobsSource,
    "Remote.co": RemoteCoSource,
    "GOV.UK Find a Job": GovUKFindAJobSource,
    # ── Scraper (no key, needs python-jobspy installed) ───────
    "JobSpy": JobSpySource,
    "LinkedIn": LinkedInSource,
    "LinkedIn (Direct)": LinkedInDirectSource,
    # ── Free API key required ─────────────────────────────────
    "Adzuna": AdzunaSource,
    "Reed": ReedSource,
    "USAJobs": USAJobsSource,
    "Jooble": JoobleSource,
    "Google Jobs": SerpAPIGoogleJobsSource,
    "Findwork": FindworkSource,
    "CareerJet": CareerJetSource,
    "JobData": JobDataSource,
}

FREE_SOURCES = [
    "RemoteOK", "Arbeitnow", "The Muse", "Jobicy",
    "Remotive", "WeWorkRemotely", "WorkingNomads", "Lobsters",
    "Greenhouse", "Lever", "Ashby", "Workable",
    "JobsCollider", "DevITjobs",
    "HN Who is hiring", "Totaljobs", "Remote.co",
    "GOV.UK Find a Job", "JobSpy", "LinkedIn", "LinkedIn (Direct)",
]
API_KEY_SOURCES = [
    "Adzuna", "Reed", "USAJobs", "Jooble",
    "Google Jobs", "Findwork", "CareerJet", "JobData",
]
