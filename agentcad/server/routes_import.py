"""Reference-import upload route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.imports import MAX_IMPORT_BYTES, safe_import_name
from ..core.model import ValidationError


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/imports")
    async def upload_import(proj: str, request: Request, filename: str):
        name = safe_import_name(filename)
        body = await request.body()
        if len(body) > MAX_IMPORT_BYTES:
            raise ValidationError("import exceeds the 100 MB limit")
        if not body:
            raise ValidationError("empty upload")
        # write=True: the upload is a persistent mutation, so it answers to
        # the write guard (branch checkout + turn lock) like a script write.
        dest = service.store.imports_dir(proj, write=True) / name
        dest.write_bytes(body)
        return {"source": name, "size_bytes": len(body)}

    return router
