"""PDF page rendering only; not the document-processing SDK or extraction.

API/resource/threading contract verified against pypdfium2's official docs:
https://pypdfium2.readthedocs.io/en/stable/python_api.html
"""
from contextlib import contextmanager
from html import escape
from io import BytesIO
import math
from pathlib import Path
from threading import RLock
from urllib.parse import quote

import pypdfium2 as pdfium

# PDFium is not thread-safe even for separate documents. All calls and closes
# stay inside this process lock; nothing shares a live native handle afterward.
PDF_LOCK = RLock()


@contextmanager
def opened_pdf(path: Path):
    with PDF_LOCK:
        try:
            document = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as exc:
            raise ValueError("PDF を開けません。暗号化・破損の有無を原本で確認してください") from exc
        try:
            if not 0 < len(document) <= 500:
                raise ValueError("PDF プレビューは 1～500 ページの原本に対応しています")
            yield document
        finally:
            document.close()


def pdf_html(path: Path, filename: str, document_id: str, page_number: int) -> str:
    with opened_pdf(path) as document:
        total = len(document)
        if not 1 <= page_number <= total:
            raise ValueError("指定したページが原本にありません")
    base = "/api/documents/" + quote(document_id, safe="")
    navigation = []
    if page_number > 1:
        navigation.append(f'<a href="{base}/preview?page={page_number - 1}">← 前のページ</a>')
    if page_number < total:
        navigation.append(f'<a href="{base}/preview?page={page_number + 1}">次のページ →</a>')
    return (f'<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(filename)}</title>'
            '<style>body{font:14px system-ui;background:#eef2f0;color:#19332f;margin:20px}h1{font-size:18px}'
            'nav{display:flex;gap:24px;margin:16px 0}img{display:block;max-width:100%;height:auto;background:white;box-shadow:0 1px 8px #bac9c1}'
            'a{color:#17675e}p{line-height:1.7}</style>'
            f'<h1>{escape(filename)} — 原本 {page_number} / {total} ページ</h1>'
            '<p>原本ページの画像プレビューです。この表示自体は SDK の抽出結果ではありません。指定された原本ページを表示しています。</p>'
            f'<nav>{" ".join(navigation)}</nav>'
            f'<img id="pdf-page-image" src="{base}/pages/{page_number}.png" alt="原本 {page_number} ページ">'
            '</html>')


def pdf_page_png(path: Path, page_number: int) -> bytes:
    with opened_pdf(path) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError("指定したページが原本にありません")
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                raise ValueError("PDF ページサイズを確認できません")
            bitmap = page.render(scale=min(1.5, 2400 / max(width, height)), may_draw_forms=False)
            try:
                image = bitmap.to_pil()
                try:
                    output = BytesIO()
                    image.save(output, format="PNG")
                    return output.getvalue()
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
