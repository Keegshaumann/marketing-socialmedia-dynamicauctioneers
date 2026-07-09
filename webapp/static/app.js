/* Dynamic Auctioneers Platform - minimal client glue.
   Everything degrades: no framework, no build step. HTMX drives interactivity;
   this file only wires drag-drop, toast lifecycle, and a small HTMX config.
   All motion is CSS; JS never animates. Respects prefers-reduced-motion via CSS. */
(function () {
  'use strict';

  // ---- HTMX config (runs once htmx is present) --------------------------
  function configHtmx() {
    if (!window.htmx) return;
    // let CSS swap classes settle long enough to be seen (matches --dur-3)
    window.htmx.config.defaultSwapStyle = 'innerHTML';
    window.htmx.config.defaultSettleDelay = 20;
    window.htmx.config.globalViewTransitions = false;
  }

  // ---- Drag-drop intake -------------------------------------------------
  // Wires any [data-dropzone] element to a hidden file <input> (data-input).
  // On drop: fills the input and submits the closest form (HTMX handles POST).
  function wireDropzones(root) {
    (root || document).querySelectorAll('[data-dropzone]').forEach(function (zone) {
      if (zone.__wired) return;
      zone.__wired = true;
      var input = zone.querySelector('input[type=file]') ||
                  document.getElementById(zone.getAttribute('data-input') || '');

      function stop(e) { e.preventDefault(); e.stopPropagation(); }

      ['dragenter', 'dragover'].forEach(function (ev) {
        zone.addEventListener(ev, function (e) { stop(e); zone.classList.add('is-dragover'); });
      });
      ['dragleave', 'dragend'].forEach(function (ev) {
        zone.addEventListener(ev, function (e) { stop(e); zone.classList.remove('is-dragover'); });
      });
      zone.addEventListener('drop', function (e) {
        stop(e);
        zone.classList.remove('is-dragover');
        if (input && e.dataTransfer && e.dataTransfer.files.length) {
          input.files = e.dataTransfer.files;
          input.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
      // keyboard + click accessibility: activate the file picker
      zone.addEventListener('click', function () { if (input) input.click(); });
      zone.addEventListener('keydown', function (e) {
        if ((e.key === 'Enter' || e.key === ' ') && input) { e.preventDefault(); input.click(); }
      });
    });
  }

  // ---- Toasts -----------------------------------------------------------
  // Auto-dismiss any .toast after a timeout. New toasts arrive via HTMX OOB
  // swaps into #toasts; we observe additions and schedule their removal.
  function dismiss(node, delay) {
    setTimeout(function () {
      node.style.transition = 'opacity 200ms cubic-bezier(0.23,1,0.32,1), transform 200ms cubic-bezier(0.23,1,0.32,1)';
      node.style.opacity = '0';
      node.style.transform = 'translateY(8px)';
      setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 220);
    }, delay || 4200);
  }
  function armToasts(root) {
    (root || document).querySelectorAll('.toast:not([data-armed])').forEach(function (t) {
      t.setAttribute('data-armed', '1');
      dismiss(t, parseInt(t.getAttribute('data-ttl') || '4200', 10));
    });
  }

  // ---- Action-bar gold sweep on approval press --------------------------
  // Any element with [data-gold-sweep] triggers the sweep on the .actionbar.
  function wireSweeps(root) {
    (root || document).querySelectorAll('[data-gold-sweep]').forEach(function (btn) {
      if (btn.__sweep) return;
      btn.__sweep = true;
      btn.addEventListener('click', function () {
        var bar = btn.closest('.actionbar') || document.querySelector('.actionbar');
        if (!bar) return;
        bar.classList.add('gold-sweep', 'is-sweeping');
        setTimeout(function () { bar.classList.remove('is-sweeping'); }, 560);
      });
    });
  }

  function init(root) { wireDropzones(root); armToasts(root); wireSweeps(root); }

  document.addEventListener('DOMContentLoaded', function () { configHtmx(); init(document); });
  // re-wire content swapped in by HTMX
  document.body && document.body.addEventListener &&
    document.addEventListener('htmx:afterSwap', function (e) { init(e.target || document); });
  document.addEventListener('htmx:load', function (e) { init(e.target || document); });
})();
