"""The photo picker accumulates across batches (fix list 1.1, D70).

A file input REPLACES its FileList on every pick. Drop three photos, then drop
two more, and the first three are gone before Upload is ever pressed. The
marketer's photos live in several folders, so picking one lot and then another is
the normal way to use it, and the app was silently discarding all but the last.

The fault and the fix are both in the browser, so the test runs in one: the real
`webapp/static/app.js` is loaded against the panel's markup and files are dropped
the way a person drops them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js"

PANEL = """<!doctype html><meta charset="utf-8">
<div id="photos">
  <div class="dropzone" data-dropzone tabindex="0">
    <input type="file" id="photo-files" name="files" accept="image/*" multiple data-photo-input hidden>
    <div class="dropzone__files" data-dropzone-files></div>
  </div>
  <button data-photo-clear>Clear</button>
  <div data-photo-pending hidden>
    <div data-photo-pending-title></div><div data-photo-pending-names></div>
  </div>
  <div data-photo-empty></div>
  <button data-photo-submit></button>
</div>"""

# Drops files onto the zone exactly as a browser would, and reports what the
# input holds afterwards.
DROP = """(names) => {
  const dt = new DataTransfer();
  names.forEach(n => dt.items.add(new File([new Uint8Array(10)], n, {type: 'image/png'})));
  document.querySelector('[data-dropzone]').dispatchEvent(
    new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt}));
  return Array.from(document.querySelector('[data-photo-input]').files).map(f => f.name);
}"""


@pytest.fixture()
def panel():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - environment without playwright
        pytest.skip("Playwright not installed; the picker runs in a browser")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        try:
            page = browser.new_page()
            page.set_content(PANEL)
            page.add_script_tag(content=APP_JS.read_text(encoding="utf-8"))
            # app.js wires on DOMContentLoaded, already fired by now.
            page.evaluate("() => document.dispatchEvent(new Event('DOMContentLoaded'))")
            yield page
        finally:
            browser.close()


def test_each_drop_adds_to_the_ones_already_chosen(panel):
    assert panel.evaluate(DROP, ["kitchen.png", "lounge.png"]) == ["kitchen.png", "lounge.png"]
    assert panel.evaluate(DROP, ["garden.png"]) == ["kitchen.png", "lounge.png", "garden.png"]
    assert panel.evaluate(DROP, ["patio.png", "pool.png"]) == [
        "kitchen.png", "lounge.png", "garden.png", "patio.png", "pool.png"
    ]


def test_the_same_photograph_twice_is_one_photograph(panel):
    """Keyed on name and size, not the timestamp: the same picture copied into
    two folders has two timestamps and is still one picture."""
    panel.evaluate(DROP, ["garden.png", "patio.png"])
    assert panel.evaluate(DROP, ["garden.png", "pool.png"]) == [
        "garden.png", "patio.png", "pool.png"
    ]


def test_the_running_total_is_what_the_marketer_sees(panel):
    panel.evaluate(DROP, ["a.png", "b.png"])
    panel.evaluate(DROP, ["c.png"])
    assert "3 photos chosen" in panel.inner_text("[data-photo-pending-title]")
    names = panel.inner_text("[data-photo-pending-names]")
    assert "a.png" in names and "c.png" in names


def test_clear_empties_the_selection(panel):
    panel.evaluate(DROP, ["a.png", "b.png"])
    panel.click("[data-photo-clear]")
    assert panel.evaluate(
        "() => Array.from(document.querySelector('[data-photo-input]').files).length"
    ) == 0
