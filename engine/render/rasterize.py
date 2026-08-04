"""HTML -> PNG rasterisation for the branded-ad export (D39).

The marketer emails the branded ad to clients as a file attachment (never a
link), so the ad must be a real image. The ad uses modern CSS (Grid for the
hero, facts, feature columns and gallery), so it needs a current engine to
render faithfully -- an old-WebKit tool (wkhtmltoimage) drops Grid and the
layout collapses. This drives headless Chromium via Playwright and captures just
the ad card (the ``.sheet`` element), so the export is a clean, tight image.

Degrades honestly: if Playwright or its Chromium is not installed, ``html_to_png``
raises ``RasterizeUnavailable`` and the caller surfaces a clear message instead
of crashing.
"""

from __future__ import annotations

from pathlib import Path

# The ad card element to capture. Every ad design wraps its layout in one of
# these root classes (.sheet on Classic, .card on Bold Dark, .ig on the social
# designs, .ad reserved). Each root is a fixed 1080x1350 (Instagram 4:5) box, so
# the element screenshot below is exactly that size. A template with none falls
# back to a full-page screenshot.
_AD_SELECTOR = ".sheet, .card, .ig, .ad"
# The Instagram post canvas every ad renders at: 1080x1350 CSS px (4:5). The
# viewport matches so the fixed-size root lays out at its true size; the element
# screenshot at 2x device scale then yields a crisp 2160x2700 PNG.
_AD_W, _AD_H = 1080, 1350
_DEFAULT_WIDTH = _AD_W


class RasterizeUnavailable(RuntimeError):
    """Playwright or its Chromium browser is not installed on this host."""


def available() -> bool:
    """True if the Playwright Python package is importable. (Chromium presence is
    only known at launch; a missing browser surfaces as RasterizeUnavailable.)"""
    try:
        import playwright.sync_api  # noqa: F401

        return True
    except Exception:
        return False


def html_to_png(
    html_path,
    png_path,
    width: int = _DEFAULT_WIDTH,
    timeout_ms: int = 30000,
    selector: str = _AD_SELECTOR,
) -> Path:
    """Rasterise a local ad HTML file to a PNG and return the PNG path.

    Loads the file (so the ad's relative photo paths resolve), waits for it to
    settle, and screenshots the ``.sheet`` card at 2x for a crisp image. Raises
    ``RasterizeUnavailable`` when Playwright/Chromium is missing and
    ``RuntimeError`` on any other render failure.

    ``selector`` overrides the element captured, for non-ad documents whose root
    class differs (the artifact-pack thumbnails pass a wider list). Anything that
    matches nothing still falls back to a full-page screenshot.
    """
    html_path = Path(html_path).resolve()  # as_uri() needs an absolute path
    png_path = Path(png_path)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # package not installed
        raise RasterizeUnavailable(
            "Playwright is not installed. Run `pip install playwright` and "
            "`playwright install chromium` to enable the ad PNG export."
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
            try:
                page = browser.new_page(
                    viewport={"width": width, "height": _AD_H}, device_scale_factor=2
                )
                page.goto(html_path.as_uri(), wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(200)  # let images paint
                target = page.locator(selector or _AD_SELECTOR)
                if target.count() > 0:
                    target.first.screenshot(path=str(png_path))
                else:  # no .sheet wrapper -> full page fallback
                    page.screenshot(path=str(png_path), full_page=True)
            finally:
                browser.close()
    except RasterizeUnavailable:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            raise RasterizeUnavailable(
                "Chromium is not installed for Playwright. Run "
                "`playwright install chromium` on the server."
            ) from exc
        raise RuntimeError(f"ad rasterisation failed: {msg[:300]}") from exc

    if not png_path.exists():
        raise RuntimeError("ad rasterisation produced no file")
    return png_path


def html_to_pdf(html_path, pdf_path, timeout_ms: int = 60000) -> Path:
    """Print a local HTML document to a real A4 PDF and return its path.

    For the buyer information pack, which is a multi-page DOCUMENT rather than a
    fixed-canvas ad: a buyer is emailed a PDF, not a web page. Chromium's own
    print pipeline is used, so the template's ``@media print`` rules apply and
    backgrounds are kept (``print_background``), which the pack's ivory paper and
    gold rules depend on.

    Raises ``RasterizeUnavailable`` when Playwright/Chromium is missing, so the
    caller can fall back to serving the HTML rather than failing the render.
    """
    html_path = Path(html_path).resolve()  # as_uri() needs an absolute path
    pdf_path = Path(pdf_path)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RasterizeUnavailable(
            "Playwright is not installed. Run `pip install playwright` and "
            "`playwright install chromium` to enable the PDF export."
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
            try:
                page = browser.new_page()
                page.goto(html_path.as_uri(), wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(250)  # let photos paint before printing
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "12mm", "bottom": "14mm", "left": "10mm", "right": "10mm"},
                )
            finally:
                browser.close()
    except RasterizeUnavailable:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            raise RasterizeUnavailable(
                "Chromium is not installed for Playwright. Run "
                "`playwright install chromium` on the server."
            ) from exc
        raise RuntimeError(f"PDF export failed: {msg[:300]}") from exc

    if not pdf_path.exists():
        raise RuntimeError("PDF export produced no file")
    return pdf_path
