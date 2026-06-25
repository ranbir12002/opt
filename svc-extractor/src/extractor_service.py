# src/extractor_service.py

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .extractor_engine import run_extraction
from .extractor_utils import ServiceConfig, setup_logging, now_ms, to_jsonable


def create_app() -> FastAPI:
    setup_logging("svc-extractor")
    log = logging.getLogger(__name__)

    app = FastAPI(
        title="Optificial Extractor Service",
        version="0.1.0",
        description="Reusable document extraction microservice (PDF/XLSX/CSV/DOCX/Images)",
    )

    # CORS (keep permissive for dev; lock down later)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/extract")
    async def extract(
        file: UploadFile = File(...),
        doc_type_hint: str = Form("unknown"),
        enable_ocr: Optional[bool] = Form(None),
        max_ocr_pages: Optional[int] = Form(None),
    ):
        """
        Multipart upload endpoint.
        Returns normalized extraction JSON:
          - status, is_useful, confidence, warnings/errors
          - text/pages/tables/key_values
        """
        t0 = now_ms()

        if not file.filename:
            raise HTTPException(status_code=400, detail="Missing filename")

        # Limit upload size (simple check based on read bytes)
        file_bytes = await file.read()

        max_bytes = ServiceConfig.MAX_UPLOAD_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Limit is {ServiceConfig.MAX_UPLOAD_MB} MB",
            )

        ocr_flag = ServiceConfig.ENABLE_OCR_DEFAULT if enable_ocr is None else bool(enable_ocr)
        ocr_pages = ServiceConfig.PDF_MAX_OCR_PAGES if max_ocr_pages is None else int(max_ocr_pages)

        try:
            result = run_extraction(
                file_bytes=file_bytes,
                filename=file.filename,
                content_type=file.content_type,
                doc_type_hint=doc_type_hint,
                enable_ocr=ocr_flag,
                max_ocr_pages=ocr_pages,
            )

            payload = to_jsonable(result)
            payload["timing_ms"] = now_ms() - t0

            # Log minimal summary (do not log full text)
            log.info(
                f"extract ok file={file.filename} type={payload.get('detected_type')} "
                f"useful={payload.get('is_useful')} conf={payload.get('confidence')} "
                f"warnings={len(payload.get('warnings', []))} errors={len(payload.get('errors', []))} "
                f"ms={payload['timing_ms']}"
            )

            return JSONResponse(content=payload)

        except Exception as e:
            log.exception(f"extract failed file={file.filename}: {e}")
            raise HTTPException(status_code=500, detail=f"Extraction failed: {type(e).__name__}: {e}")

    return app


app = create_app()
