"""Reference-import upload + structured-preview routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from ..kernel.client import KernelError
from ..kernel.protocol import ERROR_CONTRACT as CONTRACT_ERROR
from ..core.imports import MAX_IMPORT_BYTES, safe_import_name
from ..core.model import NotFoundError, ValidationError
# One list of the extensions that can carry a product tree, on this side of
# the OCP wall (the kernel handler's own copy lives in a module the server
# process may not import) — and the auto-detect itself, so the dialog offers a
# structured landing exactly when `import_cad_file` would choose one.
from ..core.tools_import import (INSPECT_TIMEOUT_S, STRUCTURED_EXTS,
                                 looks_structured)


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

    @router.post("/projects/{proj}/imports/{name}/preview")
    def preview_import(proj: str, name: str):
        """The uploaded file's product tree (PRD-017 FR8) — read-only.

        What the import dialog asks before deciding between a structured and a
        flat landing, and the same walk `import_cad_file`'s auto-detect runs.
        Writes nothing: the read-side `imports_dir`, no `create_part`, no
        `.brep` materialization.

        The response carries `structured_suggested`: the tool's OWN auto-detect
        (`tools_import.looks_structured`) over this payload, so the dialog
        offers a structured landing exactly when the import would pick one. It
        is a shared function and not a second rule — the re-imported AgentCAD
        widget (N anonymous `SOLID` products) previews `false` for the same
        reason the import lands it flat.

        Refusals: an unusable filename or a format with no product tree is a
        422 (`safe_import_name` / the extension check), a file that is not
        there is a 404, and a file the walk cannot read is **also a 422**.

        That last one is a judgement, and it is the `routes_drawing` rule read
        honestly rather than by its status code. A 502 says "the kernel is in a
        bad way"; every refusal the walk RAISES ITSELF (`contract_error`: not a
        STEP, an unreadable file, a transfer that moved no shapes, no products)
        is a statement about the bytes the caller uploaded, which they can act
        on — so it is a 422 carrying the worker's message verbatim. A
        `kernel_error`, a timeout or a crash is still the worker's problem and
        still a 502: those keep the `routes_drawing` mapping unchanged.
        """
        safe = safe_import_name(name)
        if Path(safe).suffix.lower() not in STRUCTURED_EXTS:
            raise ValidationError(
                f"{Path(safe).suffix.lower()} files carry no product tree; "
                f"previewable formats: {', '.join(sorted(STRUCTURED_EXTS))}",
                {"source": safe, "supported": sorted(STRUCTURED_EXTS)},
            )
        path = service.store.imports_dir(proj) / safe
        if not path.is_file():
            raise NotFoundError(
                f"no imported file {safe!r} in project {proj!r}; upload it first"
            )
        try:
            payload = service.kernel.request(
                "inspect_cad_tree", {"source_path": str(path)},
                timeout_s=INSPECT_TIMEOUT_S,
            )
        except KernelError as exc:
            if exc.type != CONTRACT_ERROR:
                raise                       # kernel state — app.py's 502
            raise ValidationError(
                f"could not read the product tree of {safe!r}: {exc.message}",
                {"source": safe, "kernel_error": exc.type},
            ) from exc
        payload["structured_suggested"] = looks_structured(payload)
        return payload

    return router
