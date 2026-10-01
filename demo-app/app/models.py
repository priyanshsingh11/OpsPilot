"""Data models and seed data for the synthetic recruitment application.

This is a synthetic demo app: all data lives in memory and is re-seeded on
every process start. No real candidate data is used anywhere.
"""

from dataclasses import dataclass, field


CANDIDATE_STATUSES = [
    "applied",
    "screening",
    "shortlisted",
    "interview",
    "offer",
    "hired",
    "rejected",
]

INTERVIEW_STATUSES = ["scheduled", "completed", "cancelled"]


@dataclass
class Job:
    id: str
    title: str
    department: str
    location: str
    status: str  # "open" | "closed"
    hiring_manager: str


@dataclass
class Candidate:
    id: str
    name: str
    email: str
    job_id: str
    status: str
    experience_years: int
    skills: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class Interview:
    id: str
    candidate_id: str
    job_id: str
    round: str
    scheduled_at: str  # ISO 8601
    interviewer: str
    status: str = "scheduled"


JOBS: list[Job] = [
    Job(
        id="job-ai-engineer",
        title="AI Engineer",
        department="Engineering",
        location="Remote",
        status="open",
        hiring_manager="Priya Nair",
    ),
    Job(
        id="job-backend-engineer",
        title="Backend Engineer",
        department="Engineering",
        location="Bengaluru",
        status="open",
        hiring_manager="Rahul Verma",
    ),
    Job(
        id="job-data-scientist",
        title="Data Scientist",
        department="Data",
        location="Remote",
        status="open",
        hiring_manager="Ananya Iyer",
    ),
    Job(
        id="job-product-manager",
        title="Product Manager",
        department="Product",
        location="Mumbai",
        status="open",
        hiring_manager="Vikram Mehta",
    ),
    Job(
        id="job-ml-engineer",
        title="ML Engineer",
        department="Engineering",
        location="Remote",
        status="closed",
        hiring_manager="Priya Nair",
    ),
]

CANDIDATES: list[Candidate] = [
    # --- AI Engineer pipeline ---
    Candidate(
        id="cand-aarav",
        name="Aarav Sharma",
        email="aarav.sharma@example.com",
        job_id="job-ai-engineer",
        status="shortlisted",
        experience_years=4,
        skills=["Python", "PyTorch", "LLM fine-tuning", "RAG"],
        summary="Built a RAG support assistant at a fintech; shipped fine-tuned "
        "models to production serving 200k requests/day.",
    ),
    Candidate(
        id="cand-diya",
        name="Diya Patel",
        email="diya.patel@example.com",
        job_id="job-ai-engineer",
        status="shortlisted",
        experience_years=3,
        skills=["Python", "TensorFlow", "MLOps", "Kubernetes"],
        summary="Led ML platform work at a health-tech startup; strong on "
        "evaluation harnesses and model monitoring.",
    ),
    Candidate(
        id="cand-rohan",
        name="Rohan Gupta",
        email="rohan.gupta@example.com",
        job_id="job-ai-engineer",
        status="applied",
        experience_years=2,
        skills=["Python", "scikit-learn", "SQL"],
        summary="Data analyst moving into ML; strong fundamentals, limited "
        "production experience.",
    ),
    Candidate(
        id="cand-sneha",
        name="Sneha Reddy",
        email="sneha.reddy@example.com",
        job_id="job-ai-engineer",
        status="interview",
        experience_years=5,
        skills=["Python", "JAX", "Distributed training", "CUDA"],
        summary="Systems-heavy ML background; previously trained 10B+ parameter "
        "models on large GPU clusters.",
    ),
    Candidate(
        id="cand-kabir",
        name="Kabir Singh",
        email="kabir.singh@example.com",
        job_id="job-ai-engineer",
        status="rejected",
        experience_years=1,
        skills=["Python", "pandas"],
        summary="Recent bootcamp graduate; not enough depth for this role.",
    ),
    # --- Backend Engineer pipeline ---
    Candidate(
        id="cand-ishaan",
        name="Ishaan Mehta",
        email="ishaan.mehta@example.com",
        job_id="job-backend-engineer",
        status="shortlisted",
        experience_years=6,
        skills=["Go", "PostgreSQL", "Kafka", "AWS"],
        summary="Built payment processing services handling $2B annual volume.",
    ),
    Candidate(
        id="cand-mira",
        name="Mira Nair",
        email="mira.nair@example.com",
        job_id="job-backend-engineer",
        status="applied",
        experience_years=3,
        skills=["Java", "Spring", "MySQL"],
        summary="Solid enterprise backend experience; some exposure to event-driven design.",
    ),
    # --- Data Scientist pipeline ---
    Candidate(
        id="cand-arjun",
        name="Arjun Desai",
        email="arjun.desai@example.com",
        job_id="job-data-scientist",
        status="shortlisted",
        experience_years=5,
        skills=["Python", "Statistics", "dbt", "A/B testing"],
        summary="Experimentation lead at an e-commerce company; built the pricing "
        "optimization model still in use today.",
    ),
    # --- Product Manager pipeline ---
    Candidate(
        id="cand-tara",
        name="Tara Kapoor",
        email="tara.kapoor@example.com",
        job_id="job-product-manager",
        status="screening",
        experience_years=7,
        skills=["Product strategy", "B2B SaaS", "Roadmapping"],
        summary="Shipped developer-tooling products used by 40k engineering teams.",
    ),
]

INTERVIEWS: list[Interview] = [
    Interview(
        id="int-seed-1",
        candidate_id="cand-sneha",
        job_id="job-ai-engineer",
        round="Technical Screen",
        scheduled_at="2026-10-06T14:00:00",
        interviewer="Priya Nair",
        status="scheduled",
    ),
]
