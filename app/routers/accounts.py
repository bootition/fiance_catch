from fastapi import APIRouter, Form, HTTPException


router = APIRouter(tags=["Accounts"])


@router.post("/accounts")
def create_account_route(
    name: str | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = (name, start, end, show_archived, lang)
    raise HTTPException(status_code=404, detail="account management disabled")


@router.post("/accounts/{account_id}/rename")
def rename_account_route(
    account_id: int,
    name: str | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = (account_id, name, start, end, show_archived, lang)
    raise HTTPException(status_code=404, detail="account management disabled")


@router.post("/accounts/{account_id}/archive")
def archive_account_route(
    account_id: int,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = (account_id, start, end, show_archived, lang)
    raise HTTPException(status_code=404, detail="account management disabled")


@router.post("/accounts/{account_id}/restore")
def restore_account_route(
    account_id: int,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = (account_id, start, end, show_archived, lang)
    raise HTTPException(status_code=404, detail="account management disabled")


@router.post("/accounts/{account_id}/delete")
def delete_account_route(
    account_id: int,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = (account_id, start, end, show_archived, lang)
    raise HTTPException(status_code=404, detail="account management disabled")
