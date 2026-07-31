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
    // htmx 2.x does not swap 4xx responses by default, which would silently hide
    // our guard/validation partials (the distribute 409 blocks, intake 400,
    // gate sign-off refusals). Swap 4xx bodies into the target so the user sees
    // why the action stopped; leave 5xx as genuine errors (no swap).
    document.body.addEventListener('htmx:beforeSwap', function (e) {
      var status = e.detail.xhr && e.detail.xhr.status;
      if (status >= 400 && status < 500) {
        e.detail.shouldSwap = true;
        e.detail.isError = false;
      }
    });
  }

  // ---- Drag-drop intake -------------------------------------------------
  // Wires any [data-dropzone] element to a hidden file <input> (data-input).
  // On drop/browse: fills the input and shows the chosen files IN the zone, so
  // the user can confirm the pair before pressing Upload (the form submits on
  // click, not on change — no silent auto-upload).
  var _FILE_SVG = '<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3v4a1 1 0 0 0 1 1h4"></path><path d="M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2z"></path></svg>';
  var _CHECK_SVG = '<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5l10 -10"></path></svg>';

  function _fmtSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  // Render the selected files into the dropzone as confirmation chips.
  // Names use textContent (never innerHTML), so filenames can't inject markup.
  function renderDropzoneFiles(zone, input) {
    var box = zone.querySelector('[data-dropzone-files]');
    if (!box) return;
    var files = (input && input.files) ? input.files : [];
    var form = zone.closest('form');
    var submit = form ? form.querySelector('[data-intake-submit]') : null;

    box.textContent = '';
    if (!files.length) {
      zone.classList.remove('is-filled');
      if (submit) submit.classList.remove('is-ready');
      return;
    }

    zone.classList.add('is-filled');
    if (submit) submit.classList.add('is-ready');

    var n = files.length;
    var ok = (n === 2 || n === 3);  // EVM + Property Report, plus an optional valuation
    var head = document.createElement('div');
    head.className = 'dropzone__files-head ' + (ok ? 'is-ok' : 'is-note');
    head.innerHTML = (ok ? _CHECK_SVG : '');
    var headText = document.createElement('span');
    headText.textContent = n === 1 ? '1 file selected' : n + ' files ready';
    head.appendChild(headText);
    box.appendChild(head);

    Array.prototype.forEach.call(files, function (f) {
      var row = document.createElement('div');
      row.className = 'dropzone__file';
      var ic = document.createElement('span');
      ic.className = 'dropzone__file-icon';
      ic.innerHTML = _FILE_SVG;
      var name = document.createElement('span');
      name.className = 'dropzone__file-name';
      name.textContent = f.name;
      var size = document.createElement('span');
      size.className = 'dropzone__file-size';
      size.textContent = _fmtSize(f.size);
      row.appendChild(ic);
      row.appendChild(name);
      row.appendChild(size);
      box.appendChild(row);
    });

    if (!ok) {
      var note = document.createElement('div');
      note.className = 'dropzone__files-note';
      note.textContent = n < 2
        ? 'Add the second PDF - the Lightstone EVM and the Property Report make the pair.'
        : 'That is more than three PDFs. The engine expects the EVM, the Property Report, and at most one valuation report.';
      box.appendChild(note);
    }
  }

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
      // show the picked files (drop dispatches 'change' on the input too)
      if (input) {
        input.addEventListener('change', function () { renderDropzoneFiles(zone, input); });
        renderDropzoneFiles(zone, input);
      }
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

  // ---- Email the ad ------------------------------------------------------
  // Browsers can't attach a file to a new email for us, so the button does the
  // next best thing: download the ad PNG, then open a pre-filled compose window
  // for the marketer to attach the just-downloaded file (one drag).
  function wireEmailAd(root) {
    (root || document).querySelectorAll('[data-email-ad]').forEach(function (btn) {
      if (btn.__emailad) return;
      btn.__emailad = true;
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var png = btn.getAttribute('data-png');
        var subject = btn.getAttribute('data-subject') || '';
        var body = btn.getAttribute('data-body') || '';
        if (png) {
          var a = document.createElement('a');
          a.href = png; a.download = '';
          document.body.appendChild(a); a.click(); a.remove();
        }
        // give the download a beat to start before the mail client steals focus
        setTimeout(function () {
          window.location.href = 'mailto:?subject=' +
            encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
        }, 600);
      });
    });
  }

  // ---- Ad-design picker: optimistic active highlight on click -----------
  // The click also hx-posts to apply + re-render the ad; this just moves the
  // "selected" ring immediately so it doesn't wait for the swap.
  function wireAdtpl(root) {
    (root || document).querySelectorAll('[data-adtpl-group]').forEach(function (grp) {
      if (grp.__adtpl) return;
      grp.__adtpl = true;
      grp.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest('.adtpl');
        if (!btn || !grp.contains(btn)) return;
        grp.querySelectorAll('.adtpl').forEach(function (b) { b.classList.remove('is-active'); });
        btn.classList.add('is-active');
      });
    });
  }

  // Show the auction-details panel only when the sale method is Auction.
  function wireAuctionPanel(root) {
    (root || document).querySelectorAll('[data-method-select]').forEach(function (sel) {
      if (sel.__auction) return;
      sel.__auction = true;
      var panel = document.querySelector(sel.getAttribute('data-auction-target') || '#auction-panel');
      if (!panel) return;
      var sync = function () { panel.hidden = (sel.value !== 'auction'); };
      sel.addEventListener('change', sync);
      sync();
    });
  }

  function init(root) { wireDropzones(root); armToasts(root); wireSweeps(root); wireEmailAd(root); wireAdtpl(root); wireAuctionPanel(root); }

  // A plain (non-HTMX) form submit does a full-page nav; swap the submit
  // button's label for its spinner so the click has immediate feedback. HTMX
  // forms get .htmx-request instead, so they are skipped here.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || form.nodeName !== 'FORM') return;
    if (form.hasAttribute('hx-post') || form.hasAttribute('hx-get') ||
        form.hasAttribute('hx-put') || form.hasAttribute('hx-delete')) return;
    var btn = form.querySelector('[type="submit"]');
    if (btn) btn.classList.add('is-submitting');
  });

  document.addEventListener('DOMContentLoaded', function () { configHtmx(); init(document); });
  // re-wire content swapped in by HTMX
  document.body && document.body.addEventListener &&
    document.addEventListener('htmx:afterSwap', function (e) { init(e.target || document); });
  document.addEventListener('htmx:load', function (e) { init(e.target || document); });
})();
