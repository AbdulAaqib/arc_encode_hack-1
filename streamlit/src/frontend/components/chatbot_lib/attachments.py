from __future__ import annotations

import importlib.util
import os
from io import BytesIO
from typing import Iterable


def extract_text_from_upload(upload) -> str:
    """Best-effort text extraction from Streamlit UploadedFile.
    Supports txt, md, pdf (via pypdf), docx (via python-docx), csv, json.
    Falls back to utf-8 decode for unknown types.
    """
    try:
        name = getattr(upload, "name", "")
        ext = os.path.splitext(name.lower())[1]
        data = upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
        if ext in {".txt", ".md", ".csv", ".json"}:
            try:
                return data.decode("utf-8", errors="ignore")
            except Exception:
                return ""
        if ext == ".pdf":
            spec = importlib.util.find_spec("pypdf")
            if spec is not None:  # pragma: no cover - optional dependency
                from pypdf import PdfReader  # type: ignore

                try:
                    reader = PdfReader(BytesIO(data))
                    return "\n\n".join((page.extract_text() or "") for page in reader.pages)
                except Exception:
                    return ""
            else:
                return "(PDF provided; install 'pypdf' to extract text)"
        if ext == ".docx":
            spec = importlib.util.find_spec("docx")
            if spec is not None:  # pragma: no cover - optional dependency
                from docx import Document  # type: ignore

                try:
                    doc = Document(BytesIO(data))
                    return "\n".join(p.text for p in doc.paragraphs)
                except Exception:
                    return ""
            else:
                return "(DOCX provided; install 'python-docx' to extract text)"
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    except Exception:
        return ""


def build_attachment_context(uploads, clip_len: int | None = None) -> str:
    """Compose a compact context block from uploaded docs; if clip_len is None or <=0, include full text."""
    if not uploads:
        return ""
    sections: list[str] = []
    for f in uploads:
        text = extract_text_from_upload(f)
        if not text:
            continue
        excerpt = text if not clip_len or clip_len <= 0 else text[:clip_len]
        sections.append(f"### {getattr(f, 'name', 'document')}\n{excerpt}")
    return "\n\n".join(sections)
