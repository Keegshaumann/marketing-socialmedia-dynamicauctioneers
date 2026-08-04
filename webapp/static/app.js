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
    // A pair is the minimum; a multi-portion property can carry many EVMs, so
    // any count of two or more is fine. One file just needs its partner.
    var ok = (n >= 2);
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
      note.textContent =
        'Add the second PDF - the Lightstone EVM and the Property Report make the pair.';
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
  function armToasts() {
    // Toasts always live in #toasts and arrive via an OOB swap, so the swap
    // target (e.g. #photos) never contains them. Scan the whole document; the
    // data-armed flag makes this idempotent, so re-scanning on every swap is
    // safe and guarantees a newly OOB-added toast gets its dismiss timer.
    document.querySelectorAll('.toast:not([data-armed])').forEach(function (t) {
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

  // ---- Work indicator: "it is running, and here is how long" ------------
  // A slow action (re-rendering an advert) used to look like nothing happened.
  // Any element carrying data-work now raises a labelled progress bar for the
  // life of its HTMX request. Attach it to any other slow action with attributes
  // alone - no extra markup, no extra JS:
  //   data-work       the label, e.g. "Building the advert with the Bold design"
  //   data-work-eta   optional honest hint printed under the bar
  //   data-work-into  optional selector for where the bar is placed
  // The real percentage is unknowable, so the bar creeps toward 90% on a
  // half-life curve (the shape intake's _progress() uses server side) and only
  // reaches 100% when the response lands; the elapsed counter beside the label
  // is the honest measure. Clicking twice is safe (requests are counted), and
  // the bar clears on error, on abort and on a watchdog, so it cannot stick.
  var WORK_CEIL = 90;          // % the bar approaches but never claims to pass
  var WORK_HALFLIFE = 6;       // seconds to close half the remaining distance
  var WORK_WATCHDOG = 180000;  // ms: clear even if no end event ever arrives
  var _workLive = [];          // bars with at least one request in flight

  function _workPct(secs) {
    return Math.max(4, Math.round(WORK_CEIL * (1 - Math.pow(0.5, secs / WORK_HALFLIFE))));
  }
  function _workElapsed(secs) {
    if (secs < 60) return secs + 's';
    return Math.floor(secs / 60) + ':' + ('0' + (secs % 60)).slice(-2);
  }

  // Where the bar goes: an explicit target, else the nearest declared host,
  // else the panel body the trigger sits in, else right beside the trigger.
  function _workHost(el) {
    var sel = el.getAttribute('data-work-into');
    var into = sel ? document.querySelector(sel) : null;
    return into || el.closest('[data-work-host]') || el.closest('.panel__body') || el.parentNode;
  }

  function _workBar(host) {
    if (host.__workbar && host.__workbar.parentNode === host) return host.__workbar;
    var bar = document.createElement('div');
    bar.className = 'workbar';
    bar.setAttribute('role', 'status');  // reads the label out when it appears
    bar.hidden = true;
    bar.innerHTML =
      '<div class="workbar__head">' +
        '<span class="spinner workbar__spinner" aria-hidden="true"></span>' +
        '<span class="workbar__label"></span>' +
        '<span class="workbar__elapsed" aria-hidden="true">0s</span>' +
      '</div>' +
      '<div class="progress" aria-hidden="true"><div class="progress__bar"></div></div>' +
      '<div class="hint workbar__eta" hidden></div>';
    host.appendChild(bar);
    bar.__work = { xhrs: [], tick: null, hide: null, dog: null, t0: 0, host: host };
    host.__workbar = bar;
    return bar;
  }

  function workStart(el, xhr) {
    var host = _workHost(el);
    if (!host || !host.appendChild) return;
    var bar = _workBar(host);
    var st = bar.__work;
    var fill = bar.querySelector('.progress__bar');
    var eta = bar.querySelector('.workbar__eta');
    var etaText = el.getAttribute('data-work-eta') || '';

    if (st.hide) { clearTimeout(st.hide); st.hide = null; }
    bar.classList.remove('is-done');
    // labels come from markup, so textContent (never innerHTML)
    bar.querySelector('.workbar__label').textContent = el.getAttribute('data-work') || 'Working';
    eta.textContent = etaText;
    eta.hidden = !etaText;

    if (!st.tick) {  // a fresh run, not a second click landing on a live one
      st.t0 = Date.now();
      // rewind without animating backwards from a just-finished 100%: drop the
      // transition for one frame. (CSS still owns every duration.)
      fill.style.transition = 'none';
      fill.style.width = '4%';
      void fill.offsetWidth;
      fill.style.transition = '';
      var step = function () {
        var secs = Math.max(0, Math.round((Date.now() - st.t0) / 1000));
        fill.style.width = _workPct(secs) + '%';
        bar.querySelector('.workbar__elapsed').textContent = _workElapsed(secs);
      };
      step();
      st.tick = setInterval(step, 1000);
      if (_workLive.indexOf(bar) === -1) _workLive.push(bar);
    }
    if (xhr && st.xhrs.indexOf(xhr) === -1) st.xhrs.push(xhr);
    else if (!xhr) st.xhrs.push({});
    bar.hidden = false;
    if (host.classList) host.classList.add('is-working');
    if (st.dog) clearTimeout(st.dog);
    st.dog = setTimeout(function () { workFinish(bar); }, WORK_WATCHDOG);
  }

  // One request ended. The bar only clears when the LAST in-flight request
  // ends, so a double click cannot hide it while work is still running. htmx
  // fires several end events per request (afterRequest, then responseError or
  // sendError or timeout), so matching on the xhr makes the repeats no-ops.
  function workEnd(xhr) {
    for (var i = _workLive.length - 1; i >= 0; i--) {
      var bar = _workLive[i], st = bar.__work;
      var at = xhr ? st.xhrs.indexOf(xhr) : st.xhrs.length - 1;
      if (at < 0) continue;
      st.xhrs.splice(at, 1);
      if (!st.xhrs.length) workFinish(bar);
      if (!xhr) break;  // no identity to match: end one request, newest bar only
    }
  }

  function workFinish(bar) {
    var st = bar.__work;
    var fill = bar.querySelector('.progress__bar');
    if (st.tick) { clearInterval(st.tick); st.tick = null; }
    if (st.dog) { clearTimeout(st.dog); st.dog = null; }
    st.xhrs.length = 0;
    var at = _workLive.indexOf(bar);
    if (at !== -1) _workLive.splice(at, 1);
    if (st.host && st.host.classList) st.host.classList.remove('is-working');
    bar.classList.add('is-done');  // CSS rushes the bar to full, then fades it
    fill.style.width = '100%';
    st.hide = setTimeout(function () {
      st.hide = null;
      bar.hidden = true;
      bar.classList.remove('is-done');
      fill.style.width = '4%';
    }, 560);
  }

  // ---- Photo picker: show what was chosen but not yet uploaded ----------
  // The browser's file input only reports "N files", and nothing has reached the
  // server until Upload is pressed - so the panel below would still read "No
  // photos yet" while 9 files sat in the picker. Surface the pending selection
  // (names via textContent, never innerHTML) and hide the contradicting empty
  // state until the upload actually happens.
  function wirePhotoPicker(root) {
    (root || document).querySelectorAll('[data-photo-input]').forEach(function (input) {
      if (input.__photopick) return;
      input.__photopick = true;
      var panel = input.closest('#photos') || document;
      var pending = panel.querySelector('[data-photo-pending]');
      var title = panel.querySelector('[data-photo-pending-title]');
      var names = panel.querySelector('[data-photo-pending-names]');
      var empty = panel.querySelector('[data-photo-empty]');
      var submit = panel.querySelector('[data-photo-submit]');
      if (!pending || !title || !names) return;

      input.addEventListener('change', function () {
        var files = input.files || [];
        var n = files.length;
        if (!n) {
          pending.hidden = true;
          if (empty) empty.hidden = false;
          if (submit) submit.classList.remove('is-ready');
          return;
        }
        title.textContent = n === 1
          ? '1 photo chosen, not uploaded yet'
          : n + ' photos chosen, not uploaded yet';
        var list = [];
        Array.prototype.forEach.call(files, function (f) { list.push(f.name); });
        names.textContent = list.join(', ');
        pending.hidden = false;
        // The empty state would otherwise say "No photos yet" beside this.
        if (empty) empty.hidden = true;
        if (submit) submit.classList.add('is-ready');
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

  // ---- Scaled advert preview --------------------------------------------
  // An advert is a fixed 1080x1350 page. Dropped into a short iframe you see its
  // top-left corner and nothing else, so the marketer had to open every render
  // in a new tab to judge a change. Lay the iframe out at true size and scale it
  // down to the container, keeping the whole design visible and crisp.
  function fitPreview(box) {
    var frame = box.querySelector('iframe');
    if (!frame) return;
    var w = parseFloat(box.getAttribute('data-w')) || 1080;
    var h = parseFloat(box.getAttribute('data-h')) || 1350;
    var avail = box.clientWidth;
    if (!avail) return;                       // hidden or not laid out yet
    var scale = avail / w;
    frame.style.transform = 'scale(' + scale + ')';
    box.style.height = Math.round(h * scale) + 'px';
  }

  function wirePreviewScale(root) {
    (root || document).querySelectorAll('[data-adframe]').forEach(function (box) {
      fitPreview(box);
      if (box.__fit) return;
      box.__fit = true;
      // Re-fit when the column width changes (window resize, panel reflow).
      if (window.ResizeObserver) {
        new ResizeObserver(function () { fitPreview(box); }).observe(box);
      } else {
        window.addEventListener('resize', function () { fitPreview(box); });
      }
      // The iframe reports its own load; re-fit then too, in case the design
      // swapped to a different canvas size.
      var frame = box.querySelector('iframe');
      if (frame) frame.addEventListener('load', function () { fitPreview(box); });
    });
  }

  function init(root) { wireDropzones(root); armToasts(root); wireSweeps(root); wireEmailAd(root); wireAdtpl(root); wireAuctionPanel(root); wirePhotoPicker(root); wirePreviewScale(root); }

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

  // Work indicator. htmx events bubble to document, so one delegated pair covers
  // every [data-work] trigger - including any that get swapped away mid-flight,
  // whose end event would never reach a listener bound to the element itself.
  document.addEventListener('htmx:beforeRequest', function (e) {
    var el = (e.detail && e.detail.elt) || e.target;
    var trigger = (el && el.closest) ? el.closest('[data-work]') : null;
    if (trigger) workStart(trigger, e.detail && e.detail.xhr);
  });
  ['htmx:afterRequest', 'htmx:responseError', 'htmx:sendError', 'htmx:timeout', 'htmx:sendAbort']
    .forEach(function (ev) {
      document.addEventListener(ev, function (e) { workEnd(e.detail && e.detail.xhr); });
    });

  document.addEventListener('DOMContentLoaded', function () { configHtmx(); init(document); });
  // re-wire content swapped in by HTMX
  document.body && document.body.addEventListener &&
    document.addEventListener('htmx:afterSwap', function (e) { init(e.target || document); });
  document.addEventListener('htmx:load', function (e) { init(e.target || document); });
})();
