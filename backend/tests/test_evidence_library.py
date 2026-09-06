"""The evidence library's filters: ``GET /api/cases/library/all``.

The library is the one list that spans cases, and the Evidence screen renders it
in two modes from the same route: the whole catalogue, and one case's exhibits.
The second mode is what ``case_id`` exists for.

``q`` could not serve that purpose even though it already matches ``case_id``. It
matches it as a *substring*, alongside filename, sha256 and evidence id, so it
answers "mentions this text somewhere" rather than "belongs to this case" -- and
because there is only one ``q``, using it as the scope leaves nothing to search
within the scope with. These tests pin the distinction, and pin that the filters
compose, because the screen sends scope and search together.

Every test tags its own rows: the test database is shared across the session, so
nothing here may assume the library holds only what it uploaded.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.helpers import jpeg_bytes, wav_bytes


def _upload(
    client: TestClient,
    name: str,
    *,
    seed: int = 0,
    audio: bool = False,
) -> dict:
    """Seal one exhibit into a new case and return the upload payload."""
    data = wav_bytes() if audio else jpeg_bytes(seed=seed)
    mime = "audio/wav" if audio else "image/jpeg"
    res = client.post(
        "/api/cases/upload",
        files={"file": (name, data, mime)},
        data={"title": f"Library fixture {name}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _add(client: TestClient, case_id: str, name: str, *, seed: int = 0, audio: bool = False) -> dict:
    """Seal a further exhibit into an existing case."""
    data = wav_bytes() if audio else jpeg_bytes(seed=seed)
    mime = "audio/wav" if audio else "image/jpeg"
    res = client.post(
        "/api/cases/upload",
        files={"file": (name, data, mime)},
        data={"case_id": case_id},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _library(client: TestClient, **params) -> dict:
    params.setdefault("limit", 500)
    res = client.get("/api/cases/library/all", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_case_id_scopes_the_library_to_one_case(client: TestClient) -> None:
    tag = uuid.uuid4().hex[:8]
    mine = _upload(client, f"scoped-{tag}.jpg", seed=301)
    other = _upload(client, f"other-{tag}.jpg", seed=302)
    case_id = mine["case"]["case_id"]

    scoped = _library(client, case_id=case_id)

    assert scoped["total"] == 1
    assert [e["evidence_id"] for e in scoped["evidence"]] == [
        mine["evidence"]["evidence_id"]
    ]
    # The other case's exhibit exists in the library but not in this scope.
    unscoped_ids = {e["evidence_id"] for e in _library(client)["evidence"]}
    assert other["evidence"]["evidence_id"] in unscoped_ids


def test_case_id_is_an_exact_match_not_a_substring(client: TestClient) -> None:
    """A prefix of a real case id must not be treated as that case.

    ``q`` matches ``case_id`` with ``LIKE %...%``. If ``case_id`` did the same, a
    truncated id -- the kind a URL gets when it is hand-edited or cut short --
    would silently return another case's exhibits, and the screen would print
    them under the case number the operator thinks they are looking at.
    """
    tag = uuid.uuid4().hex[:8]
    sealed = _upload(client, f"exact-{tag}.jpg", seed=303)
    case_id = sealed["case"]["case_id"]

    assert _library(client, case_id=case_id)["total"] == 1
    assert _library(client, case_id=case_id[:-4])["total"] == 0


def test_unknown_case_id_is_an_empty_list_not_an_error(client: TestClient) -> None:
    """A filter that matches nothing is answered, not refused.

    Whether a case exists is ``GET /api/cases/{id}``'s question. This route is a
    filter over the library, and the truthful answer for a case id it holds no
    evidence for is zero rows.
    """
    empty = _library(client, case_id=f"missing-{uuid.uuid4().hex}")
    assert empty["total"] == 0
    assert empty["evidence"] == []


def test_scope_composes_with_the_search_term(client: TestClient) -> None:
    """Both at once: this case's exhibits, narrowed by filename.

    This is the combination the Evidence screen sends when an operator types in
    the search box while a case is open, and the combination a single ``q`` could
    not express.
    """
    tag = uuid.uuid4().hex[:8]
    first = _upload(client, f"invoice-{tag}.jpg", seed=304)
    case_id = first["case"]["case_id"]

    # A second exhibit in the same case, with a name the query will exclude.
    _add(client, case_id, f"passport-{tag}.jpg", seed=305)

    assert _library(client, case_id=case_id)["total"] == 2

    narrowed = _library(client, case_id=case_id, q=f"invoice-{tag}")
    assert narrowed["total"] == 1
    assert narrowed["evidence"][0]["filename"] == f"invoice-{tag}.jpg"

    # The search term alone would also have matched nothing outside this case,
    # so assert the scope is doing work: a term present in *another* case is not
    # reachable through this case's scope.
    elsewhere = _upload(client, f"invoice-{tag}-elsewhere.jpg", seed=306)
    still_narrowed = _library(client, case_id=case_id, q=f"invoice-{tag}")
    assert elsewhere["evidence"]["evidence_id"] not in {
        e["evidence_id"] for e in still_narrowed["evidence"]
    }


def test_scope_composes_with_the_media_type_filter(client: TestClient) -> None:
    tag = uuid.uuid4().hex[:8]
    sealed = _upload(client, f"mixed-{tag}.jpg", seed=307)
    case_id = sealed["case"]["case_id"]

    added = _add(client, case_id, f"mixed-{tag}.wav", audio=True)
    assert added["evidence"]["media_type"] == "audio"

    assert _library(client, case_id=case_id)["total"] == 2

    images = _library(client, case_id=case_id, media_type="image")
    assert images["total"] == 1
    assert images["evidence"][0]["media_type"] == "image"

    audio = _library(client, case_id=case_id, media_type="audio")
    assert audio["total"] == 1
    assert audio["evidence"][0]["media_type"] == "audio"


def test_scoping_the_library_writes_nothing_to_the_audit_chain(
    client: TestClient,
) -> None:
    """Listing is a read. The Evidence screen calls this on mount and on every
    keystroke, so a write here would fill the case file with browsing history."""
    tag = uuid.uuid4().hex[:8]
    sealed = _upload(client, f"quiet-{tag}.jpg", seed=308)
    case_id = sealed["case"]["case_id"]

    def chain() -> tuple[int, str | None]:
        trail = client.get(f"/api/cases/{case_id}/audit").json()
        return trail["total_rows"], trail.get("head_hash")

    before = chain()
    _library(client, case_id=case_id)
    _library(client, case_id=case_id, q="quiet", media_type="image")
    assert chain() == before
