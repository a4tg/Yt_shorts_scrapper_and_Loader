from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legal_service import legal_acceptance_required, legal_config, render_legal_page


router = APIRouter(tags=["legal"])


@router.get("/api/legal/config")
def public_legal_config() -> dict[str, object]:
    config = legal_config()
    return {
        "version": config.version,
        "complete": config.complete,
        "acceptance_required": legal_acceptance_required(),
        "documents": {
            "terms": "/terms",
            "offer": "/offer",
            "privacy": "/privacy",
            "personal_data_consent": "/personal-data-consent",
            "refund_policy": "/refund-policy",
            "storage_policy": "/storage-policy",
        },
    }


def _page(page: str) -> HTMLResponse:
    return HTMLResponse(render_legal_page(page))


@router.get("/terms", response_class=HTMLResponse)
def terms() -> HTMLResponse:
    return _page("terms")


@router.get("/offer", response_class=HTMLResponse)
def offer() -> HTMLResponse:
    return _page("offer")


@router.get("/privacy", response_class=HTMLResponse)
def privacy() -> HTMLResponse:
    return _page("privacy")


@router.get("/personal-data-consent", response_class=HTMLResponse)
def personal_data_consent() -> HTMLResponse:
    return _page("personal-data-consent")


@router.get("/refund-policy", response_class=HTMLResponse)
def refund_policy() -> HTMLResponse:
    return _page("refund-policy")


@router.get("/storage-policy", response_class=HTMLResponse)
def storage_policy() -> HTMLResponse:
    return _page("storage-policy")
