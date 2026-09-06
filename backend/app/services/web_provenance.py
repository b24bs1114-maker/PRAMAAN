"""Public Web Provenance Discovery service.

Integrates Google Cloud Vision API Web Detection to discover publicly accessible web
occurrences of case evidence, verified with perceptual hashing (pHash, dHash, aHash)
and DINOv2 continuous visual embeddings.

Forensic constraints:
  - Internal corpus wording: "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS"
  - Public web discovery wording: "EARLIEST DISCOVERED PUBLIC WEB OCCURRENCE"
  - Never claims "first-ever internet appearance", "true origin", or "original upload".
  - Never invents platform names (e.g. X, Telegram, Reddit); domain is extracted strictly from URL.
  - Never invents publication timestamps.
  - If credentials or configuration are absent, gracefully reports
    "PUBLIC_WEB_DISCOVERY_UNAVAILABLE" without affecting internal provenance.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import ipaddress
import io
import logging
import socket
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import numpy as np
from PIL import Image

from app.config import Settings
from app.schemas.api import (
    WebDiscoveryResponse,
    WebDiscoverySummaryOut,
    WebEntityOut,
    WebOccurrenceOut,
    WebTimelineNodeOut,
)
from app.services.dinov2_service import extract_embedding
from app.services.hashing import (
    calculate_ahash,
    calculate_dhash,
    calculate_phash,
    hamming_distance,
)

logger = logging.getLogger("pramaan.web_provenance")

# Optional Google Cloud Vision SDK
try:
    from google.cloud import vision
    from google.oauth2 import service_account

    _HAVE_GOOGLE_VISION = True
except ImportError:  # pragma: no cover
    _HAVE_GOOGLE_VISION = False


def extract_domain(url: str) -> str:
    """Extract clean domain/host from URL, preserving real host identity."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or url
    except Exception:
        return url


def _url_is_public_http(url: str) -> bool:
    """True only for http(s) URLs whose host resolves outside private space.

    The URLs handed to the verifier come from a third-party API response and
    are therefore attacker-influenced: a page indexed by Google can point the
    PRAMAAN server at `http://169.254.169.254/`, `http://localhost:8000/...`
    or an internal service. Fetching any of those would make this process a
    second-order SSRF oracle, so every candidate URL (and every redirect
    hop) must clear this gate: http(s) scheme, a resolvable hostname, and no
    address in loopback / private / link-local / reserved ranges. DNS is
    resolved per call so a rebinding attempt cannot be cached past the check.
    On any resolution failure the URL is rejected rather than fetched.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        return False
    for info in addr_infos:
        sockaddr = info[4][0]
        try:
            ip = ipaddress.ip_address(sockaddr)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def is_web_discovery_available(settings: Settings) -> tuple[bool, str | None]:
    """Check whether public web discovery is enabled and properly credentialed."""
    if not settings.pramaan_web_discovery_enabled:
        return False, "Public web discovery is disabled (PRAMAAN_WEB_DISCOVERY_ENABLED is false)."

    creds_path = settings.google_application_credentials
    api_key = settings.google_cloud_vision_api_key.get_secret_value()

    if not creds_path and not api_key:
        return (
            False,
            "Google Cloud Vision API credentials are not configured (GOOGLE_APPLICATION_CREDENTIALS or API key required).",
        )

    if creds_path:
        path = Path(creds_path)
        if not path.is_file():
            return False, f"Credentials file specified in GOOGLE_APPLICATION_CREDENTIALS not found: {creds_path}"

    return True, None


class WebProvenanceService:
    """Discovers and verifies public web occurrences of evidentiary media."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def run_discovery(
        self,
        *,
        case_id: str,
        evidence_id: str | None = None,
        image_path: Path | str | None = None,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        vision_client: Any | None = None,
        raw_web_detection: dict[str, Any] | None = None,
    ) -> WebDiscoveryResponse:
        """Run public web detection and verification for an evidence item.

        Args:
            case_id: The PRAMAAN case UUID.
            evidence_id: Optional evidence record UUID.
            image_path: Path to local evidence image file.
            image_bytes: Optional raw image bytes.
            image_url: Optional public URL to inspect directly.
            vision_client: Optional injected Vision client for testing.
            raw_web_detection: Test-only override of the raw Vision response.
                Production callers never set it: the service calls the real
                API (or the injected ``vision_client``) and any failure is
                reported as an ERROR status, never silently replaced.
        """
        discovered_at = datetime.now(timezone.utc).isoformat()
        caveats = [
            "External provenance reflects only publicly indexed pages discovered by Google Cloud Vision.",
            "Discovery on the public web does not confirm true origin or legal ownership.",
            "Absence of web occurrences does not imply authenticity or absence of digital circulation.",
        ]

        # 1. Availability check
        available, unavailable_reason = is_web_discovery_available(self.settings)
        if not available and raw_web_detection is None:
            return WebDiscoveryResponse(
                case_id=case_id,
                evidence_id=evidence_id,
                available=False,
                status="PUBLIC_WEB_DISCOVERY_UNAVAILABLE",
                summary=WebDiscoverySummaryOut(),
                caveats=caveats,
                unavailable_reason=unavailable_reason,
            )

        # 2. Resolve image payload
        raw_bytes: bytes | None = image_bytes
        if raw_bytes is None and image_path is not None:
            p = Path(image_path)
            if p.is_file():
                raw_bytes = p.read_bytes()

        if raw_bytes is None and not image_url and raw_web_detection is None:
            return WebDiscoveryResponse(
                case_id=case_id,
                evidence_id=evidence_id,
                available=True,
                status="ERROR",
                summary=WebDiscoverySummaryOut(),
                caveats=caveats,
                unavailable_reason="No valid image bytes, local file, or image URL provided for web discovery.",
            )

        # 3. Reference hashes & embeddings for verification
        ref_phash: str | None = None
        ref_dhash: str | None = None
        ref_ahash: str | None = None
        ref_embedding: Any | None = None

        if raw_bytes:
            try:
                with Image.open(io.BytesIO(raw_bytes)) as pil_img:
                    ref_phash = calculate_phash(pil_img)
                    ref_dhash = calculate_dhash(pil_img)
                    ref_ahash = calculate_ahash(pil_img)
                ref_embedding = extract_embedding(
                    raw_bytes,
                    model_name=self.settings.dinov2_model_name,
                    device_pref=self.settings.dinov2_device,
                )
            except Exception as e:
                logger.warning("Failed to compute reference perceptual hashes/embeddings: %s", e)

        # 4. Call Google Cloud Vision Web Detection
        if raw_web_detection is None:
            try:
                raw_web_detection = self._call_google_vision_api(
                    image_bytes=raw_bytes,
                    image_url=image_url,
                    vision_client=vision_client,
                )
            except Exception as exc:
                logger.error("Google Cloud Vision Web Detection API failed: %s", exc)
                return WebDiscoveryResponse(
                    case_id=case_id,
                    evidence_id=evidence_id,
                    available=True,
                    status="ERROR",
                    summary=WebDiscoverySummaryOut(),
                    caveats=caveats,
                    unavailable_reason=f"Google Cloud Vision Web Detection failed: {str(exc)}",
                )

        # 5. Parse, Deduplicate, and Verify Occurrences
        occurrences, web_entities, best_guess_labels = self._normalize_web_detection(
            raw_detection=raw_web_detection or {},
            discovered_at=discovered_at,
            ref_phash=ref_phash,
            ref_dhash=ref_dhash,
            ref_ahash=ref_ahash,
            ref_embedding=ref_embedding,
        )

        if not occurrences:
            return WebDiscoveryResponse(
                case_id=case_id,
                evidence_id=evidence_id,
                available=True,
                status="NO_RESULTS",
                occurrences=[],
                web_entities=web_entities,
                best_guess_labels=best_guess_labels,
                timeline=[],
                summary=WebDiscoverySummaryOut(),
                caveats=caveats,
            )

        # 6. Rank occurrences & Select Earliest Discovered Occurrence
        occurrences = self._rank_occurrences(occurrences)
        earliest_occurrence = self._select_earliest_occurrence(occurrences)

        # 7. Construct Public Web Timeline
        timeline = self._build_public_timeline(occurrences, earliest_occurrence)

        # 8. Compute summary
        summary = WebDiscoverySummaryOut(
            total_occurrences=len(occurrences),
            pages_count=sum(1 for o in occurrences if o.raw_google_category == "pages_with_matching_images"),
            full_matches_count=sum(1 for o in occurrences if o.raw_google_category == "full_matching_images"),
            partial_matches_count=sum(1 for o in occurrences if o.raw_google_category == "partial_matching_images"),
            visually_similar_count=sum(1 for o in occurrences if o.raw_google_category == "visually_similar_images"),
            verified_matches_count=sum(1 for o in occurrences if o.verified),
        )

        return WebDiscoveryResponse(
            case_id=case_id,
            evidence_id=evidence_id,
            available=True,
            status="SUCCESS",
            earliest_discovered_occurrence=earliest_occurrence,
            occurrences=occurrences,
            web_entities=web_entities,
            best_guess_labels=best_guess_labels,
            timeline=timeline,
            summary=summary,
            source_image_url=image_url,
            caveats=caveats,
        )

    def _call_google_vision_api(
        self,
        *,
        image_bytes: bytes | None,
        image_url: str | None,
        vision_client: Any | None = None,
    ) -> dict[str, Any]:
        """Call Google Cloud Vision API using client library or REST fallback."""
        if vision_client is not None:
            # Injected client for testing
            return vision_client.detect_web(image_bytes=image_bytes, image_url=image_url)

        creds_path = self.settings.google_application_credentials
        api_key = self.settings.google_cloud_vision_api_key.get_secret_value()

        # Option A: Python google-cloud-vision SDK with service account credentials
        if _HAVE_GOOGLE_VISION and creds_path and Path(creds_path).is_file():
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            client = vision.ImageAnnotatorClient(credentials=credentials)

            image = vision.Image()
            if image_bytes:
                image.content = image_bytes
            elif image_url:
                image.source.image_uri = image_url
            else:
                raise ValueError("Must provide image_bytes or image_url")

            response = client.web_detection(image=image, max_results=self.settings.web_discovery_max_results)
            # Serialize proto to dict
            return type(response).to_dict(response.web_detection)

        # Option B: REST API via httpx (API key auth).
        # The key travels in the `X-Goog-Api-Key` header, never in the URL:
        # an exception raised by raise_for_status() embeds the request URL, and
        # a query-string key would be copied straight into the log.
        if api_key:
            endpoint = "https://vision.googleapis.com/v1/images:annotate"
            headers = {"X-Goog-Api-Key": api_key}
            image_payload: dict[str, Any] = {}
            if image_bytes:
                image_payload["content"] = base64.b64encode(image_bytes).decode("ascii")
            elif image_url:
                image_payload["source"] = {"imageUri": image_url}

            request_body = {
                "requests": [
                    {
                        "image": image_payload,
                        "features": [
                            {
                                "type": "WEB_DETECTION",
                                "maxResults": self.settings.web_discovery_max_results,
                            }
                        ],
                    }
                ]
            }

            with httpx.Client(timeout=10.0) as client:
                res = client.post(endpoint, json=request_body, headers=headers)
                res.raise_for_status()
                data = res.json()
                responses = data.get("responses", [])
                if responses:
                    return responses[0].get("webDetection", {})
                return {}

        raise RuntimeError("No suitable Google Cloud Vision authentication available.")

    def _normalize_web_detection(
        self,
        *,
        raw_detection: dict[str, Any],
        discovered_at: str,
        ref_phash: int | None,
        ref_dhash: int | None,
        ref_ahash: int | None,
        ref_embedding: list[float] | None,
    ) -> tuple[list[WebOccurrenceOut], list[WebEntityOut], list[str]]:
        """Normalize raw Google Web Detection JSON into deduplicated, verified occurrences."""
        occurrences: list[WebOccurrenceOut] = []
        seen_urls: set[str] = set()

        # 1. Parse entities
        web_entities: list[WebEntityOut] = []
        raw_entities = raw_detection.get("web_entities", raw_detection.get("webEntities", []))
        if isinstance(raw_entities, list):
            for ent in raw_entities:
                if isinstance(ent, dict):
                    desc = ent.get("description")
                    if desc:
                        web_entities.append(
                            WebEntityOut(
                                entity_id=ent.get("entity_id") or ent.get("entityId"),
                                description=str(desc),
                                score=float(ent["score"]) if ent.get("score") is not None else None,
                            )
                        )

        # 2. Parse best guess labels
        best_guess_labels: list[str] = []
        raw_bgl = raw_detection.get("best_guess_labels", raw_detection.get("bestGuessLabels", []))
        if isinstance(raw_bgl, list):
            for bg in raw_bgl:
                if isinstance(bg, dict):
                    lbl = bg.get("label")
                    if lbl and str(lbl).strip():
                        best_guess_labels.append(str(lbl).strip())

        # Helper to process an image item
        def process_item(
            item_url: str,
            category: str,
            title: str | None = None,
            pub_date: str | None = None,
        ):
            if not item_url or item_url in seen_urls:
                return
            seen_urls.add(item_url)

            domain = extract_domain(item_url)
            occ_id = f"web_{len(occurrences) + 1}"

            # Verify public image if enabled and item is an image url
            verified = False
            sim: float | None = None
            dist: int | None = None
            dino_sim: float | None = None
            match_basis = f"Google Cloud Vision Web Detection ({category})"
            match_type: Literal["EXACT_MATCH", "NEAR_DUPLICATE", "VISUALLY_SIMILAR", "PAGE_ONLY"] = "PAGE_ONLY"
            verif_err: str | None = None

            if self.settings.web_discovery_verify_downloads and category in (
                "full_matching_images",
                "partial_matching_images",
                "visually_similar_images",
            ):
                verif_res = self._verify_public_image(
                    item_url,
                    ref_phash=ref_phash,
                    ref_dhash=ref_dhash,
                    ref_ahash=ref_ahash,
                    ref_embedding=ref_embedding,
                )
                if verif_res["verified"]:
                    verified = True
                    sim = verif_res["similarity"]
                    dist = verif_res["perceptual_distance"]
                    dino_sim = verif_res["dinov2_similarity"]
                    match_basis = verif_res["match_basis"]
                    match_type = verif_res["match_type"]
                else:
                    verif_err = verif_res.get("error")
                    # Fallback to category based classification if verification was blocked
                    if category == "full_matching_images":
                        match_type = "NEAR_DUPLICATE"
                        match_basis = "Google Web Detection full match (unverified direct download)"
                    elif category == "partial_matching_images":
                        match_type = "VISUALLY_SIMILAR"
                        match_basis = "Google Web Detection partial match (unverified direct download)"
                    elif category == "visually_similar_images":
                        match_type = "VISUALLY_SIMILAR"
                        match_basis = "Google Web Detection visual similarity (unverified direct download)"
            else:
                if category == "full_matching_images":
                    match_type = "NEAR_DUPLICATE"
                elif category in ("partial_matching_images", "visually_similar_images"):
                    match_type = "VISUALLY_SIMILAR"
                else:
                    match_type = "PAGE_ONLY"

            occurrences.append(
                WebOccurrenceOut(
                    occurrence_id=occ_id,
                    url=item_url,
                    domain=domain,
                    page_title=title,
                    match_type=match_type,
                    raw_google_category=category,
                    similarity=sim,
                    perceptual_distance=dist,
                    dinov2_similarity=dino_sim,
                    match_basis=match_basis,
                    published_at=pub_date,
                    discovered_at=discovered_at,
                    verified=verified,
                    verification_error=verif_err,
                )
            )

        # Process Full Matching Images
        full_imgs = raw_detection.get("full_matching_images", raw_detection.get("fullMatchingImages", []))
        if isinstance(full_imgs, list):
            for img in full_imgs:
                if isinstance(img, dict):
                    url = img.get("url")
                    if url:
                        process_item(url, "full_matching_images")

        # Process Partial Matching Images
        partial_imgs = raw_detection.get("partial_matching_images", raw_detection.get("partialMatchingImages", []))
        if isinstance(partial_imgs, list):
            for img in partial_imgs:
                if isinstance(img, dict):
                    url = img.get("url")
                    if url:
                        process_item(url, "partial_matching_images")

        # Process Pages With Matching Images
        pages = raw_detection.get("pages_with_matching_images", raw_detection.get("pagesWithMatchingImages", []))
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    page_url = page.get("url")
                    title = page.get("page_title") or page.get("pageTitle")
                    if page_url:
                        process_item(page_url, "pages_with_matching_images", title=title)

                    # Some pages embed image links directly
                    for img in page.get("full_matching_images", page.get("fullMatchingImages", [])):
                        if isinstance(img, dict) and img.get("url"):
                            process_item(img["url"], "full_matching_images", title=title)
                    for img in page.get("partial_matching_images", page.get("partialMatchingImages", [])):
                        if isinstance(img, dict) and img.get("url"):
                            process_item(img["url"], "partial_matching_images", title=title)

        # Process Visually Similar Images
        similar_imgs = raw_detection.get("visually_similar_images", raw_detection.get("visuallySimilarImages", []))
        if isinstance(similar_imgs, list):
            for img in similar_imgs:
                if isinstance(img, dict):
                    url = img.get("url")
                    if url:
                        process_item(url, "visually_similar_images")

        return occurrences, web_entities, best_guess_labels

    def _verify_public_image(
        self,
        image_url: str,
        *,
        ref_phash: str | None,
        ref_dhash: str | None,
        ref_ahash: str | None,
        ref_embedding: Any | None,
    ) -> dict[str, Any]:
        """Safely fetch public image and compute similarity verification.

        The URL comes from a third-party API response, so it is treated as
        attacker-influenced: only http(s) is honoured, hosts that resolve to
        private/loopback/link-local space are refused (second-order SSRF), and
        the body is streamed with the size cap applied during the read rather
        than after the whole file is in memory.
        """
        if ref_phash is None and ref_embedding is None:
            return {"verified": False, "error": "No reference hashes available for verification."}

        if not _url_is_public_http(image_url):
            return {"verified": False, "error": "URL rejected: only public http(s) endpoints are fetched."}

        try:
            headers = {"User-Agent": "PRAMAAN-Forensic-Integrity-Agent/1.0 (offline forensic verification)"}
            timeout = self.settings.web_discovery_fetch_timeout_seconds
            max_bytes = self.settings.web_discovery_max_download_bytes

            with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                with client.stream("GET", image_url) as stream:
                    if stream.status_code != 200:
                        return {"verified": False, "error": f"HTTP status {stream.status_code}"}
                    # Re-check the scheme/host on every redirect hop the client
                    # followed, not just the first URL we were handed.
                    if not _url_is_public_http(str(stream.url)):
                        return {"verified": False, "error": "Redirect to a non-public endpoint rejected."}
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in stream.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            return {"verified": False, "error": f"Image exceeds size cap ({max_bytes} bytes)"}
                        chunks.append(chunk)
                    body = b"".join(chunks)

                with Image.open(io.BytesIO(body)) as pil_img:
                    cand_phash = calculate_phash(pil_img)
                    cand_dhash = calculate_dhash(pil_img)
                    cand_ahash = calculate_ahash(pil_img)

                cand_emb = extract_embedding(
                    body,
                    model_name=self.settings.dinov2_model_name,
                    device_pref=self.settings.dinov2_device,
                )

            # Compute perceptual distances. A missing reference hash is NOT a
            # measurement of maximal distance -- substituting 64 would turn
            # "no data" into "maximally dissimilar", the exact confusion this
            # codebase's honesty rules exist to prevent. Only hashes that were
            # actually computed take part; the basis string discloses which.
            p_dist = hamming_distance(ref_phash, cand_phash) if ref_phash is not None else None
            d_dist = hamming_distance(ref_dhash, cand_dhash) if ref_dhash is not None else None
            a_dist = hamming_distance(ref_ahash, cand_ahash) if ref_ahash is not None else None

            measured = [d for d in (p_dist, d_dist, a_dist) if d is not None]
            effective_dist: int | None = min(measured) if measured else None

            # Compute DINOv2 cosine similarity
            dino_sim: float | None = None
            if ref_embedding is not None and cand_emb is not None:
                cos = float(np.dot(ref_embedding, cand_emb))
                dino_sim = max(0.0, min(1.0, cos))

            # Fused similarity: computed only from measurements that exist.
            # An unmeasured leg is excluded from the mean, never substituted.
            parts: list[float] = []
            if effective_dist is not None:
                parts.append(max(0.0, 1.0 - (effective_dist / 64.0)))
            if dino_sim is not None:
                parts.append(dino_sim)
            fused_sim: float | None = (
                round(sum(parts) / len(parts), 4) if parts else None
            )

            # Categorize match
            if effective_dist == 0 or (effective_dist is not None and effective_dist <= 2 and (dino_sim or 0.0) >= 0.98):
                m_type: Literal["EXACT_MATCH", "NEAR_DUPLICATE", "VISUALLY_SIMILAR", "PAGE_ONLY"] = "EXACT_MATCH"
            elif (effective_dist is not None and effective_dist <= 12) or ((dino_sim or 0.0) >= 0.85):
                m_type = "NEAR_DUPLICATE"
            elif (dino_sim or 0.0) >= 0.70 or (effective_dist is not None and effective_dist <= 20):
                m_type = "VISUALLY_SIMILAR"
            else:
                m_type = "PAGE_ONLY"

            basis_bits = [
                f"perceptual dist {effective_dist}" if effective_dist is not None else "perceptual dist not measured",
                f"DINOv2 sim {dino_sim:.3f}" if dino_sim is not None else "DINOv2 sim not measured",
            ]
            match_basis = " + ".join(basis_bits)

            return {
                "verified": True,
                "similarity": fused_sim,
                "perceptual_distance": effective_dist,
                "dinov2_similarity": round(dino_sim, 4) if dino_sim is not None else None,
                "match_type": m_type,
                "match_basis": match_basis,
            }
        except Exception as e:
            return {"verified": False, "error": f"Verification error: {str(e)}"}

    def _rank_occurrences(self, occurrences: list[WebOccurrenceOut]) -> list[WebOccurrenceOut]:
        """Rank occurrences deterministically: exact match > near duplicate > visually similar > page only."""
        priority = {
            "EXACT_MATCH": 0,
            "NEAR_DUPLICATE": 1,
            "VISUALLY_SIMILAR": 2,
            "PAGE_ONLY": 3,
        }

        def sort_key(occ: WebOccurrenceOut):
            p = priority.get(occ.match_type, 99)
            sim = -(occ.similarity or 0.0)
            # Earlier published dates rank higher
            pub = occ.published_at or "9999-99-99"
            return (p, sim, pub, occ.url)

        return sorted(occurrences, key=sort_key)

    def _select_earliest_occurrence(self, occurrences: list[WebOccurrenceOut]) -> WebOccurrenceOut | None:
        """Select the EARLIEST DISCOVERED PUBLIC WEB OCCURRENCE."""
        if not occurrences:
            return None

        # If any occurrences carry actual publication dates, pick the earliest among matches
        dated = [o for o in occurrences if o.published_at is not None]
        if dated:
            dated.sort(key=lambda o: (o.published_at or "", o.similarity or 0.0))
            return dated[0]

        # Otherwise, the top-ranked match discovered first
        return occurrences[0]

    def _build_public_timeline(
        self,
        occurrences: list[WebOccurrenceOut],
        earliest: WebOccurrenceOut | None,
    ) -> list[WebTimelineNodeOut]:
        """Build public web occurrence timeline nodes."""
        timeline: list[WebTimelineNodeOut] = []
        for idx, occ in enumerate(occurrences):
            ts = occ.published_at or occ.discovered_at
            ts_type: Literal["published", "discovered"] = "published" if occ.published_at else "discovered"
            is_earliest = earliest is not None and occ.occurrence_id == earliest.occurrence_id

            label = f"Web Occurrence {idx + 1}: {occ.domain}"
            if occ.page_title:
                label = f"{occ.domain} - {occ.page_title[:45]}"

            timeline.append(
                WebTimelineNodeOut(
                    node_id=f"public_node_{idx + 1}",
                    url=occ.url,
                    domain=occ.domain,
                    label=label,
                    timestamp=ts,
                    timestamp_type=ts_type,
                    match_type=occ.match_type,
                    similarity=occ.similarity,
                    is_earliest=is_earliest,
                )
            )

        # Sort timeline chronologically
        timeline.sort(key=lambda n: (n.timestamp, -(n.similarity or 0.0)))
        return timeline
