/* The upload queue.
   Files start uploading the moment they're chosen — a guest should be able to
   put their phone back in a pocket without tapping a second button. */

(function () {
  var cfg = window.WEDDING || {};
  var NAME_KEY = 'wedding_guest_name';
  var CONCURRENCY = 2;              // phone uplinks hate more than this

  var els = {
    name: document.getElementById('guestName'),
    pickBtn: document.getElementById('pickBtn'),
    cameraBtn: document.getElementById('cameraBtn'),
    filePick: document.getElementById('filePick'),
    fileCam: document.getElementById('fileCam'),
    queue: document.getElementById('queue'),
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

  /* ---------------------------------------------------------- name memory */

  try {
    var saved = localStorage.getItem(NAME_KEY);
    if (saved) els.name.value = saved;
  } catch (e) { /* private browsing */ }

  els.name.addEventListener('change', function () {
    try { localStorage.setItem(NAME_KEY, els.name.value.trim()); } catch (e) {}
  });

  /* ------------------------------------------------------- camera support */

  // getUserMedia and capture= only work in a secure context. Showing a button
  // that silently does nothing is worse than not showing it.
  if (window.isSecureContext) {
    els.cameraBtn.classList.remove('hidden');
  }

  els.pickBtn.addEventListener('click', function () { els.filePick.click(); });
  els.cameraBtn.addEventListener('click', function () { els.fileCam.click(); });

  els.filePick.addEventListener('change', function () { accept(this.files); this.value = ''; });
  els.fileCam.addEventListener('change', function () { accept(this.files); this.value = ''; });

  /* ------------------------------------------------------------- queueing */

  function accept(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;

    if (files.length > cfg.maxFiles) {
      toast('Sending the first ' + cfg.maxFiles + ' — add the rest after.');
      files = files.slice(0, cfg.maxFiles);
    }
    files.forEach(add);
    pump();
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
    thumbnail(item);

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

  /* ----------------------------------------------------------- thumbnails */

  function thumbnail(item) {
    var img = item.el.querySelector('.thumb');
    var url = URL.createObjectURL(item.file);

    if (!item.isVideo) {
      img.src = url;
      img.onload = function () { URL.revokeObjectURL(url); };
      img.onerror = function () { URL.revokeObjectURL(url); filmGlyph(img); };
      return;
    }

    // Grab a frame so videos aren't anonymous grey boxes in the queue.
    var video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    var settled = false;
    var done = function (ok) {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      if (!ok) filmGlyph(img);
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
    form.append('table_id', cfg.tableId || '');

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
    form.append('table_id', cfg.tableId || '');
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
