"""Driver for the synthetic recruitment app (demo-app/main.py).

The agent operates the app through the same HTML pages and form posts a person
uses in the browser, and reads what is on screen via the pages' data-testid
hooks. A browser driver (e.g. Playwright) can replace this class later behind
the same methods; the recovery logic in scheduling.py does not change.

`/api/state` is used only for independent verification, never to perform an action.
`/admin/*` is used only by the demo harness to inject failures, never by the agent's plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


class AppUnavailable(Exception):
    """The app could not be reached or returned something we cannot interpret."""


# ---- minimal DOM ----------------------------------------------------------

@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list["Node | str"] = field(default_factory=list)

    def text(self) -> str:
        parts = [c if isinstance(c, str) else c.text() for c in self.children]
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def by_testid(self, testid: str) -> "Node | None":
        return next((n for n in self.iter() if n.attrs.get("data-testid") == testid), None)

    def by_testid_prefix(self, prefix: str) -> list["Node"]:
        return [n for n in self.iter() if n.attrs.get("data-testid", "").startswith(prefix)]

    def cells(self) -> list[str]:
        return [c.text() for c in self.children if isinstance(c, Node) and c.tag in ("td", "th")]


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root


# ---- page models ----------------------------------------------------------

@dataclass
class CandidateSummary:
    id: int
    name: str
    status: str
    job_id: int


@dataclass
class InterviewRow:
    id: int
    date: str
    start_time: str
    duration_minutes: int
    interviewer: str
    status: str


@dataclass
class CandidatePage:
    id: int
    name: str
    status: str
    interviews: list[InterviewRow]
    interviewer_options: list[str]
    duration_options: list[int]


@dataclass
class SubmitResult:
    ok: bool
    http_status: int | None  # None when the request never got a response
    message: str

    @property
    def transient(self) -> bool:
        """Worth retrying (after checking state): no response, server error or 429."""
        return self.http_status is None or self.http_status >= 500 or self.http_status == 429


def _trailing_int(testid: str) -> int:
    return int(testid.rsplit("-", 1)[1])


class RecruitAppDriver:
    def __init__(self, client: httpx.Client):
        self.client = client

    @classmethod
    def connect(cls, base_url: str, timeout: float = 5.0) -> "RecruitAppDriver":
        return cls(httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False))

    def close(self) -> None:
        self.client.close()

    def _get_page(self, path: str) -> Node:
        try:
            r = self.client.get(path)
        except httpx.HTTPError as exc:
            raise AppUnavailable(f"GET {path} failed: {exc.__class__.__name__}: {exc}") from exc
        if r.status_code != 200:
            raise AppUnavailable(f"GET {path} returned HTTP {r.status_code}")
        return parse_html(r.text)

    # -- navigation / reading -------------------------------------------------

    def list_candidates(self) -> list[CandidateSummary]:
        """Open the jobs page, then every job's candidate table."""
        home = self._get_page("/")
        job_ids = [_trailing_int(n.attrs["data-testid"]) for n in home.by_testid_prefix("view-job-")]
        out: list[CandidateSummary] = []
        for job_id in job_ids:
            page = self._get_page(f"/jobs/{job_id}")
            for name_node in page.by_testid_prefix("candidate-name-"):
                cid = _trailing_int(name_node.attrs["data-testid"])
                status = page.by_testid(f"candidate-status-{cid}")
                out.append(CandidateSummary(cid, name_node.text(), status.text() if status else "", job_id))
        return out

    def read_candidate(self, candidate_id: int) -> CandidatePage:
        page = self._get_page(f"/candidates/{candidate_id}")
        name, status = page.by_testid("candidate-name"), page.by_testid("candidate-status")
        if name is None or status is None:
            raise AppUnavailable(f"Candidate page {candidate_id} did not render the expected fields")
        rows = []
        for tr in page.by_testid_prefix("interview-row-"):
            date, start, dur, who, st = tr.cells()
            rows.append(InterviewRow(_trailing_int(tr.attrs["data-testid"]), date, start,
                                     int(dur.split()[0]), who, st))
        select = page.by_testid("interview-interviewer")
        durations = page.by_testid("interview-duration")
        return CandidatePage(
            id=candidate_id, name=name.text(), status=status.text(), interviews=rows,
            interviewer_options=[o.text() for o in select.iter() if o.tag == "option"] if select else [],
            duration_options=[int(o.attrs["value"]) for o in durations.iter() if o.tag == "option"]
            if durations else [],
        )

    # -- actions --------------------------------------------------------------

    def _post_form(self, path: str, data: dict) -> SubmitResult:
        try:
            r = self.client.post(path, data=data)
        except httpx.HTTPError as exc:
            # Timeout / connection drop: the server may or may not have applied it.
            return SubmitResult(False, None, f"{exc.__class__.__name__}: {exc}")
        if r.status_code in (302, 303):
            return SubmitResult(True, r.status_code, "Form accepted")
        banner = parse_html(r.text).by_testid("error-banner")
        return SubmitResult(False, r.status_code, banner.text() if banner else f"HTTP {r.status_code}")

    def submit_interview(self, candidate_id: int, interviewer: str, date: str,
                         start_time: str, duration_minutes: int) -> SubmitResult:
        return self._post_form(f"/candidates/{candidate_id}/interviews", {
            "interviewer": interviewer, "date": date,
            "start_time": start_time, "duration_minutes": str(duration_minutes),
        })

    def update_status(self, candidate_id: int, status: str) -> SubmitResult:
        return self._post_form(f"/candidates/{candidate_id}/status", {"status": status})

    # -- independent verification channel ---------------------------------------

    def source_of_truth(self) -> dict:
        try:
            r = self.client.get("/api/state")
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppUnavailable(f"State API unavailable: {exc}") from exc

    # -- demo harness (failure injection), not part of the agent's plan ------

    def arm_failure(self, remaining: int, mode: str) -> None:
        r = self.client.post("/admin/failure", data={"remaining": str(remaining), "mode": mode})
        if r.status_code not in (302, 303):
            raise AppUnavailable(f"Could not arm failure simulation (HTTP {r.status_code})")

    def reset_demo_data(self) -> None:
        r = self.client.post("/admin/reset")
        if r.status_code not in (302, 303):
            raise AppUnavailable(f"Could not reset demo data (HTTP {r.status_code})")
