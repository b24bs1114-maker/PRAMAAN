"""The intake contract, asserted against the API and nothing else.

Most of the suite reaches ``POST /api/cases/upload`` through the ``client``
fixture, which quietly supplies a title and an incident description when a test
leaves them out (see ``conftest.py``). That convenience exists so tests about
hashing or fusion need not fill in an intake form -- but it would also hide the
validation it stands in for. So every test here uses ``strict_client``: a plain
client that sends exactly the fields written below, and nothing more.

What is under test is the server's own authority. The frontend performs the same
checks to keep the operator from submitting a form that cannot succeed, but those
checks are a convenience; these are the gate. A request that bypasses the UI
entirely -- curl, a script, a stale build -- meets exactly this.

Covered here:

* a new case requires a non-empty title and incident description, and the
  rejection names which one is missing
* whitespace is not content
* nothing is stored when a request is rejected -- no case, no evidence
* adding evidence to an *existing* case does not re-require the intake fields
* the optional acquisition context is stored, returned, audited, and survives a
  re-read; blank means absent rather than empty
* the visible case number is compact and sequential, and is issued by the server

The authentication half of the contract lives in ``tests/test_auth.py``, which
takes the operator override off to prove the 401.
"""

from __future__ import annotations

import uuid

from tests.helpers import jpeg_bytes

TITLE = "Intake contract"
DESCRIPTION = "Why this evidence is being examined, in the operator's words."
ACQUISITION = "Handed over on a sealed USB stick by the complainant, 14:20."

REQUIRED_MESSAGE = "A new case requires a non-empty"


def _upload(client, *, seed: int, **fields):
    """One upload, sending only the fields named -- no defaults, no filling in."""
    return client.post(
        "/api/cases/upload",
        files={"file": (f"intake-{seed}.jpg", jpeg_bytes(seed=seed), "image/jpeg")},
        data={key: value for key, value in fields.items() if value is not None},
    )


def _case_count(client) -> int:
    listing = client.get("/api/cases", params={"limit": 500})
    assert listing.status_code == 200, listing.text
    return listing.json()["count"]


# --------------------------------------------------------------------------- #
# Required fields
# --------------------------------------------------------------------------- #
def test_a_new_case_needs_both_a_title_and_a_description(strict_client) -> None:
    """Sending neither is a 422 that names both, so the operator is told what is
    missing rather than that something is."""
    response = _upload(strict_client, seed=601)
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert REQUIRED_MESSAGE in message, message
    assert "case title" in message and "incident description" in message, message


def test_a_missing_title_is_named_on_its_own(strict_client) -> None:
    response = _upload(strict_client, seed=602, description=DESCRIPTION)
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert "case title" in message, message
    assert "incident description" not in message, (
        f"the description was supplied but is reported missing: {message}"
    )


def test_a_missing_description_is_named_on_its_own(strict_client) -> None:
    response = _upload(strict_client, seed=603, title=TITLE)
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert "incident description" in message, message
    assert "case title" not in message, (
        f"the title was supplied but is reported missing: {message}"
    )


def test_whitespace_is_not_content(strict_client) -> None:
    """A form filled with spaces has been filled in as far as ``required`` in HTML
    is concerned. The server disagrees, and the server decides."""
    response = _upload(strict_client, seed=604, title="   ", description="\t\n ")
    assert response.status_code == 422, response.text
    assert REQUIRED_MESSAGE in response.json()["error"]["message"]


def test_a_valid_intake_is_accepted_and_trimmed(strict_client) -> None:
    """The positive control: the same client, with the fields filled in, succeeds --
    so the rejections above are about the fields and not about this client."""
    response = _upload(
        strict_client,
        seed=605,
        title=f"  {TITLE} 605  ",
        description=f"  {DESCRIPTION}  ",
    )
    assert response.status_code == 201, response.text
    case = response.json()["case"]
    assert case["title"] == f"{TITLE} 605", repr(case["title"])
    assert case["description"] == DESCRIPTION, repr(case["description"])


# --------------------------------------------------------------------------- #
# A rejected request must leave nothing behind
# --------------------------------------------------------------------------- #
def test_a_rejected_intake_stores_nothing(strict_client) -> None:
    """The validation runs before the case is created and before the file is read,
    so a refusal cannot leave a half-open case or an orphaned blob."""
    before = _case_count(strict_client)
    assert _upload(strict_client, seed=606, title=TITLE).status_code == 422
    assert _upload(strict_client, seed=607, description=DESCRIPTION).status_code == 422
    assert _upload(strict_client, seed=608).status_code == 422
    assert _case_count(strict_client) == before, "a rejected upload created a case"


# --------------------------------------------------------------------------- #
# Adding to an existing case
# --------------------------------------------------------------------------- #
def test_adding_evidence_to_an_existing_case_needs_no_intake_fields(strict_client) -> None:
    """The intake fields describe the *case*. A second exhibit joins a case that
    already has them, so re-demanding them would be asking the operator to retype
    what the case already says."""
    opened = _upload(strict_client, seed=609, title=f"{TITLE} 609", description=DESCRIPTION)
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["case"]["case_id"]

    second = _upload(strict_client, seed=610, case_id=case_id)
    assert second.status_code == 201, second.text
    assert second.json()["case"]["case_id"] == case_id
    assert second.json()["evidence"]["case_id"] == case_id

    listing = strict_client.get(f"/api/cases/{case_id}/evidence")
    assert listing.status_code == 200, listing.text
    assert listing.json()["count"] == 2


def test_an_unknown_case_id_is_a_404_not_a_new_case(strict_client) -> None:
    """Otherwise a typo silently opens a case with no title and no description --
    exactly what the required fields are there to prevent."""
    response = _upload(strict_client, seed=611, case_id=str(uuid.uuid4()))
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# Acquisition context: optional, but real when given
# --------------------------------------------------------------------------- #
def test_acquisition_context_is_stored_and_returned(strict_client) -> None:
    """It is a column, not a label: what the operator typed comes back on the
    evidence record, from a re-read as well as from the upload response."""
    response = _upload(
        strict_client,
        seed=612,
        title=f"{TITLE} 612",
        description=DESCRIPTION,
        acquisition_context=ACQUISITION,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["evidence"]["acquisition_context"] == ACQUISITION

    listing = strict_client.get(f"/api/cases/{body['case']['case_id']}/evidence")
    assert listing.status_code == 200, listing.text
    stored = listing.json()["evidence"][0]
    assert stored["acquisition_context"] == ACQUISITION, stored.get("acquisition_context")


def test_acquisition_context_is_recorded_in_the_audit_trail(strict_client) -> None:
    """How evidence was acquired is a custody fact, so it belongs in the chain and
    not only in a mutable column."""
    marker = f"Collected in person, ref {uuid.uuid4().hex[:8]}"
    response = _upload(
        strict_client,
        seed=613,
        title=f"{TITLE} 613",
        description=DESCRIPTION,
        acquisition_context=marker,
    )
    assert response.status_code == 201, response.text
    case_id = response.json()["case"]["case_id"]

    trail = strict_client.get("/api/audit", params={"case_id": case_id, "limit": 100})
    assert trail.status_code == 200, trail.text
    ingested = [e for e in trail.json()["events"] if e.get("event") == "EVIDENCE_INGESTED"]
    assert ingested, "no ingestion event was recorded"
    assert any(
        (event.get("details") or {}).get("acquisition_context") == marker
        for event in ingested
    ), f"the acquisition context is missing from the audit details: {ingested}"


def test_omitted_or_blank_acquisition_context_is_absent_not_empty(strict_client) -> None:
    """An optional field left alone must read as "not recorded", so a report cannot
    print an empty custody note as though something had been said."""
    omitted = _upload(
        strict_client, seed=614, title=f"{TITLE} 614", description=DESCRIPTION
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["evidence"]["acquisition_context"] is None

    blank = _upload(
        strict_client,
        seed=615,
        title=f"{TITLE} 615",
        description=DESCRIPTION,
        acquisition_context="",
    )
    assert blank.status_code == 201, blank.text
    assert blank.json()["evidence"]["acquisition_context"] is None


# --------------------------------------------------------------------------- #
# The visible case number
# --------------------------------------------------------------------------- #
def test_the_case_number_is_compact_sequential_and_issued_by_the_server(
    strict_client,
) -> None:
    """It is what an operator reads out over the phone, so it is short: a prefix
    and one number, never a UUID. The client does not get to propose one."""
    first = _upload(
        strict_client,
        seed=616,
        title=f"{TITLE} 616",
        description=DESCRIPTION,
        case_number="PRAMAAN-9999999",
    )
    assert first.status_code == 201, first.text
    second = _upload(
        strict_client, seed=617, title=f"{TITLE} 617", description=DESCRIPTION
    )
    assert second.status_code == 201, second.text

    numbers = [first.json()["case"]["case_number"], second.json()["case"]["case_number"]]
    for number in numbers:
        assert number.startswith("PRAMAAN-"), number
        sequence = number.removeprefix("PRAMAAN-")
        assert sequence.isdigit(), f"{number} is not PRAMAAN-<digits>"
        assert int(sequence) > 1000, number
        # A UUID would be 32 hex characters; this has to stay readable aloud.
        assert len(sequence) <= 7, f"{number} is too long to be a case number"

    assert numbers[0] != "PRAMAAN-9999999", "the client dictated its own case number"
    assert int(numbers[1].removeprefix("PRAMAAN-")) == int(
        numbers[0].removeprefix("PRAMAAN-")
    ) + 1, f"the sequence is not consecutive: {numbers}"


def test_the_case_number_is_not_the_internal_identifier(strict_client) -> None:
    """Two identifiers, two jobs: the UUID is what the API is addressed by, the
    case number is what a person quotes. Neither may become the other."""
    response = _upload(
        strict_client, seed=618, title=f"{TITLE} 618", description=DESCRIPTION
    )
    assert response.status_code == 201, response.text
    case = response.json()["case"]
    assert case["case_id"] != case["case_number"]
    assert uuid.UUID(case["case_id"]), "the internal id is not a UUID"
    assert case["case_number"] not in case["case_id"]
