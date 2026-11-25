/* Ad Studio (D127): scaled live previews, refresh after an edit, copy buttons,
   and the property search on the list. */
(function () {
  'use strict';

  // --- previews: each iframe is the real file at full size, scaled to fit ---
  function scale() {
    document.querySelectorAll('.pv').forEach(function (pv) {
      var w = +pv.dataset.w, h = +pv.dataset.h, f = pv.querySelector('iframe');
      if (!f || !w) return;
      var s = pv.clientWidth / w;
      f.style.transform = 'scale(' + s + ')';
      if (pv.dataset.pack) {
        // The pack scrolls inside its frame: show about a page and a half, and
        // make the frame exactly that tall so the last page can be reached.
        var box = Math.min(Math.round(1123 * 1.5 * s), 980);
        pv.style.height = box + 'px';
        f.style.height = Math.round(box / s) + 'px';
        return;
      }
      pv.style.height = Math.round(h * s) + 'px';
    });
  }
  window.addEventListener('resize', scale);
  document.addEventListener('DOMContentLoaded', scale);

  var timer = null;
  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      document.querySelectorAll('.pv iframe').forEach(function (f) {
        var keep = 0;
        try { keep = f.contentWindow.scrollY; } catch (e) { /* not loaded yet */ }
        f.addEventListener('load', function once() {
          f.removeEventListener('load', once);
          try { f.contentWindow.scrollTo(0, keep); } catch (e) { /* ignore */ }
        });
        f.src = f.src.split('#')[0].replace(/([?&])t=\d+/, '') + (f.src.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now();
      });
    }, 250);
  }
  document.body.addEventListener('studio-refresh', refresh);

  // --- "Ask for a change": Cmd/Ctrl+Enter sends it ---
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey) || !t.closest || !t.closest('.studio-ask__form')) return;
    e.preventDefault();
    t.closest('form').requestSubmit();
  });

  // --- copy buttons ---
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-copy]');
    if (!b) return;
    var src = document.querySelector(b.dataset.copy);
    if (!src) return;
    navigator.clipboard.writeText(src.value).then(function () {
      var was = b.textContent; b.textContent = 'Copied'; setTimeout(function () { b.textContent = was; }, 1500);
    });
  });

  // --- downloads take a few seconds: say so ---
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-busy]');
    if (!a) return;
    var label = a.querySelector('span') || a, was = label.textContent;
    label.textContent = a.dataset.busy;
    a.setAttribute('aria-disabled', 'true');
    setTimeout(function () { label.textContent = was; a.removeAttribute('aria-disabled'); }, 9000);
  });

  // --- search on the list ---
  var q = document.querySelector('[data-studio-search]');
  if (q) {
    var groups = document.querySelector('[data-studio-groups]'),
        all = document.querySelector('[data-studio-all]'),
        none = document.querySelector('[data-studio-none]');
    q.addEventListener('input', function () {
      var term = q.value.trim().toLowerCase();
      if (!term) { groups.hidden = false; all.hidden = true; return; }
      groups.hidden = true; all.hidden = false;
      var shown = 0;
      all.querySelectorAll('.studio-row').forEach(function (r) {
        var hit = r.dataset.search.indexOf(term) >= 0;
        r.hidden = !hit; if (hit) shown++;
      });
      none.hidden = shown > 0;
    });
    q.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      var first = all.querySelector('.studio-row:not([hidden])');
      if (first) window.location = first.href;
    });
  }
})();

// Previews of the team's files and the studio's saved ones: hide one SharePoint
// has no picture for, rather than show a broken image.
(function () {
  function mark(img) { img.classList.add('is-missing'); }
  document.querySelectorAll('img[data-thumb]').forEach(function (img) {
    if (img.complete && img.naturalWidth === 0) { mark(img); }
    img.addEventListener('error', function () { mark(img); });
  });
})();
