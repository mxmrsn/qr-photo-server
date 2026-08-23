/* The upload queue.
   Files start uploading the moment they're chosen — a guest should be able to
   put their phone back in a pocket without tapping a second button. */

(function () {
  var cfg = window.WEDDING || {};
  var NAME_KEY = 'wedding_guest_name';
  var CONCURRENCY = 2;              // phone uplinks hate more than this

  var els = {
    name: document.getElementById('guestName'),
    matches: document.getElementById('nameMatches'),
    seated: document.getElementById('seated'),
    pickBtn: document.getElementById('pickBtn'),
    cameraBtn: document.getElementById('cameraBtn'),
    filePick: document.getElementById('filePick'),
    fileCam: document.getElementById('fileCam'),
    queue: document.getElementById('queue'),
    preparing: document.getElementById('preparing'),
    preparingText: document.getElementById('preparingText'),
    namePrompt: document.getElementById('namePrompt'),
    lateName: document.getElementById('lateName'),
    saveName: document.getElementById('saveName'),
    noteText: document.getElementById('noteText'),
    noteBtn: document.getElementById('noteBtn')
  };

  var queue = [];
  var active = 0;
  var uploadedIds = [];
  var counter = 0;

  /* ------------------------------------------- name, and the seating chart */

  var SEAT_KEY = 'wedding_guest_seat';
  var picked = null;          // {id, name, table_name} once chosen from the chart
  var rosterExists = false;

  function remember() {
    try {
      localStorage.setItem(NAME_KEY, els.name.value.trim());
      if (picked) localStorage.setItem(SEAT_KEY, JSON.stringify(picked));
      else localStorage.removeItem(SEAT_KEY);
    } catch (e) { /* private browsing */ }
  }

  try {
    var saved = localStorage.getItem(NAME_KEY);
    if (saved) els.name.value = saved;
    var seat = localStorage.getItem(SEAT_KEY);
    if (seat) { picked = JSON.parse(seat); showSeat(); }
  } catch (e) { /* private browsing */ }

  els.name.addEventListener('change', remember);

  function showSeat() {
    if (!picked) { els.seated.classList.remove('show'); return; }
    var where = picked.table_name
      ? (/^\d+$/.test(picked.table_name) ? 'Table ' + picked.table_name : picked.table_name)
      : null;
    els.seated.innerHTML = where
      ? '<b>' + esc(where) + '</b><a id="notMe">not you?</a>'
      : '<b>' + esc(picked.name) + '</b><a id="notMe">not you?</a>';
    els.seated.classList.add('show');
    var undo = document.getElementById('notMe');
    if (undo) undo.addEventListener('click', function () {
      picked = null;
      els.seated.classList.remove('show');
      remember();
      els.name.focus();
    });
  }

  function closeMatches() {
    els.matches.classList.remove('open');
    els.matches.innerHTML = '';
    els.name.setAttribute('aria-expanded', 'false');
  }

  function choose(g) {
    picked = { id: g.id, name: g.name, table_name: g.table_name };
    els.name.value = g.name;
    closeMatches();
    showSeat();
    remember();
  }

  function renderMatches(list, query) {
    if (!list.length) {
      // Not everyone is on the chart — a plus-one shouldn't hit a dead end.
      els.matches.innerHTML =
        '<div class="none">No match on the seating chart — that\'s fine, ' +
        'just leave your name as you typed it.</div>';
      els.matches.classList.add('open');
      return;
    }
    els.matches.innerHTML = '';
    list.forEach(function (g) {
      var b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'option');
      var where = g.table_name
        ? (/^\d+$/.test(g.table_name) ? 'Table ' + g.table_name : g.table_name)
        : '';
      b.innerHTML = '<span>' + esc(g.name) + '</span>' +
                    (where ? '<span class="tbl">' + esc(where) + '</span>' : '');
      b.addEventListener('click', function () { choose(g); });
      els.matches.appendChild(b);
    });
    els.matches.classList.add('open');
    els.name.setAttribute('aria-expanded', 'true');
  }

  var lookupTimer = null;
  var lastQuery = '';

  function lookup() {
    var q = els.name.value.trim();
    if (picked && q !== picked.name) { picked = null; els.seated.classList.remove('show'); }
    if (!rosterExists || q.length < 2) { closeMatches(); return; }
    if (q === lastQuery) return;
    lastQuery = q;
    getJSON('/api/guests?q=' + encodeURIComponent(q))
      .then(function (d) {
        if (els.name.value.trim() !== q) return;   // typed on since
        renderMatches(d.items || [], q);
      })
      .catch(function () { closeMatches(); });
  }

  els.name.addEventListener('input', function () {
    clearTimeout(lookupTimer);
    lookupTimer = setTimeout(lookup, 160);
  });
  els.name.addEventListener('focus', lookup);
  els.name.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeMatches();
    if (e.key === 'Enter') { closeMatches(); els.name.blur(); }
  });
  document.addEventListener('click', function (e) {
    if (!els.matches.contains(e.target) && e.target !== els.name) closeMatches();
  });

  // Only offer the search if there's actually a chart loaded.
  getJSON('/api/guests/enabled')
    .then(function (d) {
      rosterExists = !!(d.enabled && d.count > 0);
      if (rosterExists) {
        els.name.placeholder = 'Start typing — we\'ll find your table';
      }
    })
    .catch(function () {});

  /* ------------------------------------------------------- camera support */

  // getUserMedia and capture= only work in a secure context. Showing a button
  // that silently does nothing is worse than not showing it.
  if (window.isSecureContext) {
    els.cameraBtn.classList.remove('hidden');
  }

  var prepareTimer = null;
  var prepareEscalate = null;

  var PREPARING_NORMAL =
    "Getting your photos ready — this can take a moment for a big batch";
  // If the phone is still chewing after this long it is almost certainly
  // pulling originals down from iCloud, which cannot finish on a network with
  // no internet. Saying so turns an unexplained freeze into something the
  // guest can actually act on.
  var PREPARING_STUCK =
    "Still waiting on your phone. If your photos are stored in iCloud rather " +
    "than on the device, they may not download here — try a photo you took " +
    "today, or ask us about the wifi.";

  function showPreparing() {
    // Only after a beat — for one small photo the hand-off is instant and a
    // flash of spinner is worse than nothing.
    clearTimeout(prepareTimer);
    clearTimeout(prepareEscalate);
    els.preparingText.textContent = PREPARING_NORMAL;
    prepareTimer = setTimeout(function () {
      els.preparing.classList.add('show');
    }, 900);
    prepareEscalate = setTimeout(function () {
      els.preparingText.textContent = PREPARING_STUCK;
    }, 20000);
  }

  function hidePreparing() {
    clearTimeout(prepareTimer);
    clearTimeout(prepareEscalate);
    els.preparing.classList.remove('show');
    els.preparingText.textContent = PREPARING_NORMAL;
  }

  els.pickBtn.addEventListener('click', function () {
    showPreparing();
    els.filePick.click();
  });
  els.cameraBtn.addEventListener('click', function () {
    showPreparing();
    els.fileCam.click();
  });

  // If they backed out of the picker without choosing anything, stop hinting.
  window.addEventListener('focus', function () {
    setTimeout(function () {
      if (!els.filePick.files.length && !els.fileCam.files.length) hidePreparing();
    }, 1200);
  });

  els.filePick.addEventListener('change', function () { accept(this.files); this.value = ''; });
  els.fileCam.addEventListener('change', function () { accept(this.files); this.value = ''; });

  /* ------------------------------------------------------------- queueing */

  function accept(fileList) {
    hidePreparing();
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;

    if (files.length > cfg.maxFiles) {
      toast('Sending the first ' + cfg.maxFiles + ' — add the rest after.');
      files = files.slice(0, cfg.maxFiles);
    }

    // Order matters here. Every file is queued and uploading BEFORE a single
    // preview is decoded, because previews are decoration and uploading is the
    // entire job. Doing it the other way round meant a big selection could
    // exhaust Safari's memory during decode and the page would reload having
    // sent nothing at all.
    var batchStart = items.length;
    files.forEach(add);
    pump();
    for (var i = batchStart; i < items.length; i++) {
      queuePreview(i, i - batchStart);
    }
    pumpPreviews();
  }

  function add(file) {
    var isVideo = /^video\//.test(file.type) || /\.(mov|mp4|m4v|webm|avi|mkv|3gp)$/i.test(file.name);
    var item = {
      key: 'q' + (++counter),
      file: file,
      isVideo: isVideo,
      status: 'waiting',
      el: null
    };
    item.el = render(item);
    els.queue.appendChild(item.el);
    items.push(item);

    if (isVideo && !cfg.allowVideo) return fail(item, 'Videos are unavailable');
    if (file.size > cfg.maxBytes) return fail(item, 'Too large (' + humanBytes(file.size) + ')');
    if (file.size === 0) return fail(item, 'Empty file');

    queue.push(item);
  }

  function render(item) {
    var row = document.createElement('div');
    row.className = 'qitem';
    row.innerHTML =
      '<img class="thumb" alt="">' +
      '<div class="meta">' +
        '<div class="name">' + esc(item.file.name || 'photo') + '</div>' +
        '<div class="bar"><i></i></div>' +
      '</div>' +
      '<div class="state">waiting</div>';
    return row;
  }

  function setState(item, text) {
    item.el.querySelector('.state').textContent = text;
  }

  function setProgress(item, fraction) {
    item.el.querySelector('.bar i').style.width = Math.round(fraction * 100) + '%';
  }

  function fail(item, reason) {
    item.status = 'error';
    item.el.classList.add('error');
    setProgress(item, 1);
    setState(item, reason);
    addRetry(item);
  }

  function addRetry(item) {
    if (item.el.querySelector('.retry')) return;
    var btn = document.createElement('button');
    btn.className = 'btn small ghost retry';
    btn.textContent = 'Retry';
    btn.style.marginLeft = '8px';
    btn.addEventListener('click', function () {
      btn.remove();
      item.el.classList.remove('error');
      item.status = 'waiting';
      setProgress(item, 0);
      setState(item, 'waiting');
      queue.push(item);
      pump();
    });
    item.el.querySelector('.state').appendChild(btn);
  }

  /* ----------------------------------------------------------- thumbnails

     Generated one at a time, after uploading has already started, and at a
     reduced decode size where the browser supports it. A phone asked to
     decode twenty full-resolution photos at once will simply die. */

  var items = [];              // every queued item, in selection order
  var previewQueue = [];
  var previewBusy = false;

  // Past this many files the previews stop being useful anyway, and the
  // cheapest decode is the one you don't do.
  var MAX_PREVIEWS = 24;

  // positionInBatch, not the global index: someone who adds twelve photos,
  // then twelve more, should get previews both times.
  function queuePreview(index, positionInBatch) {
    if (positionInBatch < MAX_PREVIEWS) previewQueue.push(index);
    else glyphOnly(index);
  }

  function glyphOnly(index) {
    var item = items[index];
    if (item) filmGlyph(item.el.querySelector('.thumb'));
  }

  function pumpPreviews() {
    if (previewBusy) return;
    var index = previewQueue.shift();
    if (index === undefined) return;
    var item = items[index];
    if (!item) { pumpPreviews(); return; }
    previewBusy = true;
    thumbnail(item, function () {
      previewBusy = false;
      // Yield to the event loop so decoding never blocks an upload callback.
      setTimeout(pumpPreviews, 0);
    });
  }


  function thumbnail(item, done) {
    var img = item.el.querySelector('.thumb');
    var finish = function () { if (done) { done(); done = null; } };

    if (!item.isVideo) {
      // createImageBitmap decodes straight to the size we need instead of
      // inflating a 12-megapixel photo into memory to draw it at 46px.
      if (window.createImageBitmap) {
        createImageBitmap(item.file, { resizeWidth: 92, resizeQuality: 'low' })
          .then(function (bitmap) {
            try {
              var canvas = document.createElement('canvas');
              canvas.width = 92;
              canvas.height = 92;
              var scale = Math.max(92 / bitmap.width, 92 / bitmap.height);
              var w = bitmap.width * scale, h = bitmap.height * scale;
              canvas.getContext('2d').drawImage(bitmap, (92 - w) / 2, (92 - h) / 2, w, h);
              img.src = canvas.toDataURL('image/jpeg', 0.7);
            } catch (e) { filmGlyph(img); }
            if (bitmap.close) bitmap.close();
            finish();
          })
          .catch(function () { objectUrlPreview(item, img, finish); });
        return;
      }
      objectUrlPreview(item, img, finish);
      return;
    }

    videoPreview(item, img, finish);
  }

  function objectUrlPreview(item, img, finish) {
    var url = URL.createObjectURL(item.file);
    var settled = false;
    var release = function (ok) {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      if (!ok) filmGlyph(img);
      finish();
    };
    img.onload = function () { release(true); };
    img.onerror = function () { release(false); };
    setTimeout(function () { release(true); }, 6000);
    img.src = url;
  }

  function videoPreview(item, img, finish) {
    var url = URL.createObjectURL(item.file);
    var video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    var settled = false;
    var done = function (ok) {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      video.removeAttribute('src');
      try { video.load(); } catch (e) {}
      if (!ok) filmGlyph(img);
      finish();
    };

    video.addEventListener('loadeddata', function () {
      try {
        var canvas = document.createElement('canvas');
        canvas.width = 92;
        canvas.height = 92;
        var ratio = Math.max(92 / video.videoWidth, 92 / video.videoHeight);
        var w = video.videoWidth * ratio, h = video.videoHeight * ratio;
        canvas.getContext('2d').drawImage(video, (92 - w) / 2, (92 - h) / 2, w, h);
        img.src = canvas.toDataURL('image/jpeg', 0.7);
        done(true);
      } catch (e) { done(false); }
    });
    video.addEventListener('error', function () { done(false); });
    setTimeout(function () { done(false); }, 4000);

    video.src = url;
    try { video.currentTime = 0.2; } catch (e) {}
  }

  function filmGlyph(img) {
    img.src = 'data:image/svg+xml;utf8,' + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ' +
      'stroke="%238a6f4e" stroke-width="1.5"><rect x="2" y="4" width="20" height="16" rx="2"/>' +
      '<path d="M10 9l5 3-5 3z" fill="%238a6f4e"/></svg>'
    );
  }

  /* -------------------------------------------------------------- pumping */

  function pump() {
    while (active < CONCURRENCY && queue.length) {
      send(queue.shift());
    }
  }

  function send(item) {
    active++;
    item.status = 'uploading';
    setState(item, '0%');

    var form = new FormData();
    form.append('file', item.file, item.file.name || 'upload');
    form.append('guest_name', els.name.value.trim());
    form.append('table_id', (picked && picked.table_name) || cfg.tableId || '');
    if (picked) form.append('guest_id', picked.id);

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload', true);

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var f = e.loaded / e.total;
      setProgress(item, f);
      setState(item, Math.round(f * 100) + '%');
    };

    xhr.onload = function () {
      active--;
      var body = {};
      try { body = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status >= 200 && xhr.status < 300 && body.ok) {
        item.status = 'done';
        item.el.classList.add('done');
        setProgress(item, 1);
        setState(item, '✓ sent');
        uploadedIds.push(body.id);
        finishedOne();
      } else {
        fail(item, body.error || ('Failed (' + xhr.status + ')'));
      }
      pump();
    };

    xhr.onerror = function () {
      active--;
      fail(item, 'Connection lost');
      pump();
    };

    xhr.ontimeout = function () {
      active--;
      fail(item, 'Timed out');
      pump();
    };

    xhr.timeout = 30 * 60 * 1000;   // big videos on slow venue wifi
    item.xhr = xhr;
    xhr.send(form);
  }

  function finishedOne() {
    if (active || queue.length) return;
    toast(uploadedIds.length === 1 ? 'Sent — thank you!' :
          'Sent ' + uploadedIds.length + ' — thank you!');
    if (!els.name.value.trim() && uploadedIds.length) {
      els.namePrompt.classList.remove('hidden');
    }
  }

  /* ---------------------------------------------------- late name tagging */

  els.saveName.addEventListener('click', function () {
    var value = els.lateName.value.trim();
    if (!value) return;
    postJSON('/api/name', { ids: uploadedIds, guest_name: value })
      .then(function () {
        els.name.value = value;
        try { localStorage.setItem(NAME_KEY, value); } catch (e) {}
        els.namePrompt.classList.add('hidden');
        toast('Thanks, ' + value + '!');
      })
      .catch(function (err) { toast(err.message); });
  });

  /* -------------------------------------------------------------- the note */

  els.noteBtn.addEventListener('click', function () {
    var text = els.noteText.value.trim();
    if (!text) { toast('Write us something first!'); return; }
    var form = new FormData();
    form.append('message', text);
    form.append('guest_name', els.name.value.trim());
    form.append('table_id', (picked && picked.table_name) || cfg.tableId || '');
    if (picked) form.append('guest_id', picked.id);
    els.noteBtn.disabled = true;
    fetch('/api/note', { method: 'POST', body: form, credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        els.noteBtn.disabled = false;
        if (!j.ok) throw new Error(j.error || 'Failed');
        els.noteText.value = '';
        toast('Note delivered ♥');
      })
      .catch(function (err) { els.noteBtn.disabled = false; toast(err.message); });
  });

  /* Don't let a guest wander off mid-upload without warning. */
  window.addEventListener('beforeunload', function (e) {
    if (active || queue.length) { e.preventDefault(); e.returnValue = ''; }
  });
})();
