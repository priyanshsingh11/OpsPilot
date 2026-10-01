"""Phase 3 workflow tests: prove the browser automation layer can
deterministically complete the recruitment workflow, including detecting
simulated failures and exposing structured information a future agent can act
on.

Workflow under test:
    1. Find shortlisted AI Engineer candidates.
    2. Create an interview for one candidate.
    3. Update the candidate status.
    4. Verify the interview exists.

Failure tests:
    - Chaos mode (outage): every create fails; nothing is saved.
    - Failure plan, before_write: the next N creates fail; nothing is saved.
    - Failure plan, after_write: the interview IS saved but the request still
      fails — the automation must detect the error and expose enough
      structured information for a future agent to decide what to do.
"""

from automation import RecruitmentBrowser

AI_ENGINEER_JOB = "AI Engineer"
INTERVIEW_ROUND = "Technical Screen"
INTERVIEW_WHEN = "2026-10-10T10:00"
INTERVIEWER = "Priya Nair"


def _find_shortlisted_ai_engineer(browser: RecruitmentBrowser):
    """Step 1: find shortlisted AI Engineer candidates."""
    jobs_result = browser.open_jobs()
    assert jobs_result.success, f"open_jobs failed: {jobs_result.error}"
    assert jobs_result.data, "no jobs listed"

    job_result = browser.select_job(title=AI_ENGINEER_JOB)
    assert job_result.success, f"select_job failed: {job_result.error}"
    job = job_result.data
    assert job.title == AI_ENGINEER_JOB

    candidates_result = browser.filter_candidates(job_id=job.id, status="shortlisted")
    assert candidates_result.success, f"filter_candidates failed: {candidates_result.error}"
    assert candidates_result.data, "no shortlisted candidates found for AI Engineer"
    return job, candidates_result.data


def test_happy_path_workflow(browser: RecruitmentBrowser):
    """Find shortlisted AI Engineer candidates, create an interview, update
    status, and verify the interview exists."""
    job, shortlisted = _find_shortlisted_ai_engineer(browser)
    assert len(shortlisted) >= 1
    candidate = shortlisted[0]
    print(f"\nShortlisted candidates: {[c.name for c in shortlisted]}")

    # Read full candidate profile.
    details_result = browser.get_candidate(candidate_id=candidate.id)
    assert details_result.success, f"get_candidate failed: {details_result.error}"
    assert details_result.data.status == "shortlisted"

    # Step 2: create an interview for the candidate.
    interview_result = browser.create_interview(
        candidate_id=candidate.id,
        round_name=INTERVIEW_ROUND,
        scheduled_at=INTERVIEW_WHEN,
        interviewer=INTERVIEWER,
    )
    assert interview_result.success, f"create_interview failed: {interview_result.error}"
    interview = interview_result.data
    assert interview.candidate_id == candidate.id
    assert interview.round == INTERVIEW_ROUND
    assert interview.status == "scheduled"
    assert interview_result.screenshot, "expected a screenshot after create_interview"

    # Step 3: update the candidate status to reflect the interview.
    status_result = browser.update_candidate_status(candidate_id=candidate.id, status="interview")
    assert status_result.success, f"update_candidate_status failed: {status_result.error}"
    assert status_result.data.previous_status == "shortlisted"
    assert status_result.data.new_status == "interview"

    # Step 4: verify the interview exists on the candidate's page.
    verify_result = browser.verify_interview(candidate_id=candidate.id, round_name=INTERVIEW_ROUND)
    assert verify_result.success, f"verify_interview failed: {verify_result.error}"
    assert verify_result.data.found, "interview was not found after creation"
    assert verify_result.data.interview.round == INTERVIEW_ROUND
    assert verify_result.data.interview.interviewer == INTERVIEWER
    assert verify_result.data.interview.status == "scheduled"


def test_chaos_mode_failure_and_recovery(browser: RecruitmentBrowser):
    """Chaos mode makes every interview creation fail; the automation must
    detect the failure, expose structured error information, and recover."""
    _job, shortlisted = _find_shortlisted_ai_engineer(browser)
    candidate = shortlisted[0]

    # Enable the simulated outage.
    chaos_on = browser.toggle_chaos_mode(enabled=True)
    assert chaos_on.success, f"toggle_chaos_mode(True) failed: {chaos_on.error}"

    # Attempt to create an interview — this must fail and be detected.
    failure_result = browser.create_interview(
        candidate_id=candidate.id,
        round_name="HR Screen",
        scheduled_at="2026-10-12T11:00",
        interviewer="Rahul Verma",
    )
    assert not failure_result.success, "create_interview should have failed during chaos mode"
    assert failure_result.error is not None
    assert failure_result.error.type == "server_error"
    assert failure_result.error.details.get("status_code") == "500"
    assert failure_result.error.page_url, "expected the failing page URL in the error"
    assert failure_result.screenshot, "expected a failure screenshot"
    print(f"\nDetected failure: {failure_result.error.type} — {failure_result.error.message}")

    # Recovery: disable chaos mode and retry the same action.
    chaos_off = browser.toggle_chaos_mode(enabled=False)
    assert chaos_off.success, f"toggle_chaos_mode(False) failed: {chaos_off.error}"

    retry_result = browser.create_interview(
        candidate_id=candidate.id,
        round_name="HR Screen",
        scheduled_at="2026-10-12T11:00",
        interviewer="Rahul Verma",
    )
    assert retry_result.success, f"retry after recovery failed: {retry_result.error}"
    assert retry_result.data.round == "HR Screen"

    # The failed attempt must not have created an interview (no duplicates).
    verify_result = browser.verify_interview(candidate_id=candidate.id, round_name="HR Screen")
    assert verify_result.data.found
    hr_interviews = [i for i in verify_result.data.scheduled_interviews if i.round == "HR Screen"]
    assert len(hr_interviews) == 1, "failed attempt must not have created a duplicate interview"


def test_failure_plan_before_write(browser: RecruitmentBrowser):
    """A before_write failure plan makes the next N creates fail without
    saving anything. The automation detects each failure and the interview is
    absent afterwards."""
    _job, shortlisted = _find_shortlisted_ai_engineer(browser)
    candidate = shortlisted[0]

    # Arm a failure plan: the next 2 creates fail, nothing is saved.
    arm_result = browser.arm_failure_plan(remaining=2, mode="before_write")
    assert arm_result.success, f"arm_failure_plan failed: {arm_result.error}"
    assert arm_result.data["remaining"] == 2
    assert arm_result.data["mode"] == "before_write"

    for attempt in (1, 2):
        result = browser.create_interview(
            candidate_id=candidate.id,
            round_name="Phone Screen",
            scheduled_at="2026-10-13T09:00",
            interviewer="Ananya Iyer",
        )
        assert not result.success, f"attempt {attempt} should have failed"
        assert result.error.type == "server_error"
        assert result.error.details.get("status_code") == "500"

    # After the plan is exhausted, a create succeeds.
    ok_result = browser.create_interview(
        candidate_id=candidate.id,
        round_name="Phone Screen",
        scheduled_at="2026-10-13T09:00",
        interviewer="Ananya Iyer",
    )
    assert ok_result.success, f"create after plan exhausted failed: {ok_result.error}"

    # Exactly one "Phone Screen" interview exists — the failed ones saved nothing.
    verify_result = browser.verify_interview(candidate_id=candidate.id, round_name="Phone Screen")
    assert verify_result.data.found
    phone = [i for i in verify_result.data.scheduled_interviews if i.round == "Phone Screen"]
    assert len(phone) == 1, "before_write failures must not persist any interview"


def test_failure_plan_after_write(browser: RecruitmentBrowser):
    """An after_write failure plan is the hardest case: the interview IS
    saved but the request still fails. The automation must detect the error
    and expose structured information so a future agent can decide whether
    the interview actually exists."""
    _job, shortlisted = _find_shortlisted_ai_engineer(browser)
    candidate = shortlisted[0]

    # Arm a failure plan: the next create fails, but the interview is saved.
    arm_result = browser.arm_failure_plan(remaining=1, mode="after_write")
    assert arm_result.success, f"arm_failure_plan failed: {arm_result.error}"
    assert arm_result.data["mode"] == "after_write"

    # The create fails from the browser's perspective.
    failure_result = browser.create_interview(
        candidate_id=candidate.id,
        round_name="Onsite",
        scheduled_at="2026-10-15T15:00",
        interviewer="Vikram Mehta",
    )
    assert not failure_result.success, "create should have failed in after_write mode"
    assert failure_result.error.type == "server_error"
    assert failure_result.error.details.get("status_code") == "500"
    assert failure_result.screenshot, "expected a failure screenshot"
    print(f"\nafter_write failure detected: {failure_result.error.message}")

    # The structured error tells a future agent the request failed — but the
    # interview may or may not exist. Verification reveals the truth: in
    # after_write mode the interview WAS saved despite the error.
    verify_result = browser.verify_interview(candidate_id=candidate.id, round_name="Onsite")
    assert verify_result.success
    assert verify_result.data.found, (
        "after_write mode saves the interview despite the error; "
        "verification must reveal it"
    )
    assert verify_result.data.interview.status == "scheduled"

    # A naive retry (without checking first) creates a DUPLICATE — this is
    # exactly why the structured error matters: a future agent must verify
    # before retrying, not blindly re-issue the action.
    retry_result = browser.create_interview(
        candidate_id=candidate.id,
        round_name="Onsite",
        scheduled_at="2026-10-15T15:00",
        interviewer="Vikram Mehta",
    )
    assert retry_result.success, f"retry failed: {retry_result.error}"
    final = browser.verify_interview(candidate_id=candidate.id, round_name="Onsite")
    onsite_count = len([i for i in final.data.scheduled_interviews if i.round == "Onsite"])
    assert onsite_count == 2, (
        "a blind retry after an after_write failure creates a duplicate; "
        "the structured error exists so an agent can check first"
    )
