/* The projector.

   Runs unattended for six hours: shuffles the backlog, jumps the queue when
   something new arrives, plays videos through, and never shows a black frame
   or an error. Everything degrades to "keep showing the last good photo". */

(function () {
  var cfg = window.SLIDESHOW || {};
  var DWELL = (cfg.dwell || 7) * 1000;
  var MAX_VIDEO = (cfg.maxVideo || 25) * 1000;
  var POLL_MS = 12000;

  var layers = [document.getElementById('layerA'), document.getElementById('layerB')];
  var holding = document.getElementById('holding');
  var captionEl = document.getElementById('caption');
  var subsEl = document.getElementById('subs');
  var attribEl = document.getElementById('attrib');
  var arrivalEl = document.getElementById('arrival');
  var promoEl = document.getElementById('promo');
  var pausedEl = document.getElementById('paused');

  var pool = [];            // everything we know about
  var byId = {};
  var playlist = [];        // shuffled queue of ids still to show this cycle
  var priority = [];        // fresh arrivals, shown next
  var head = 0;             // highest seq seen
  var front = 0;            // which layer is currently visible
  var timer = null;
  var paused = false;
  var current = null;
  var showCaptions = true;
  var showPromo = true;

  /* ------------------------------------------------------------- utilities */

  function shuffle(list) {
    for (var i = list.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = list[i]; list[i] = list[j]; list[j] = t;
    }
    return list;
  }

  function refillPlaylist() {
    playlist = shuffle(pool.map(function (item) { return item.id; }));
  }

  function nextItem() {
    while (priority.length) {
      var fresh = byId[priority.shift()];
      if (fresh) return fresh;
    }
    if (!playlist.length) refillPlaylist();
    while (playlist.length) {
      var item = byId[playlist.shift()];
      if (item) return item;
    }
    return null;
  }

  /* --------------------------------------------------------------- display */

  function show(item) {
    if (!item) return;
    current = item;
    holding.style.display = 'none';

    var back = layers[1 - front];
    var stale = back.querySelector('video');
    if (stale) { stale.pause(); stale.removeAttribute('src'); stale.load(); }
    back.innerHTML = '';

    if (item.kind === 'video') {
      showVideo(item, back);
    } else {
      showImage(item, back);
    }

    // Randomising the pan direction keeps a long evening from feeling loopy.
    var dx = (Math.random() * 3 - 1.5).toFixed(2) + '%';
    var dy = (Math.random() * 3 - 1.5).toFixed(2) + '%';
    back.style.setProperty('--dx', dx);
    back.style.setProperty('--dy', dy);
    back.style.setProperty('--dwell', (DWELL + 1600) + 'ms');

    layers[front].classList.remove('on');
    back.classList.add('on');
    front = 1 - front;

    subsEl.innerHTML = '';
    paintCaption(item);
    if (item.fresh) announce(item);
    preloadNext();
  }

  function showImage(item, layer) {
    var url = 'url("' + item.display + '")';
    var bg = document.createElement('div');
    bg.className = 'bg';
    bg.style.backgroundImage = url;
    var fg = document.createElement('div');
    fg.className = 'fg';
    fg.style.backgroundImage = url;
    layer.appendChild(bg);
    layer.appendChild(fg);
    schedule(DWELL);
  }

  function showVideo(item, layer) {
    var bg = document.createElement('div');
    bg.className = 'bg';
    bg.style.backgroundImage = 'url("' + item.display + '")';
    layer.appendChild(bg);

    var video = document.createElement('video');
    video.src = item.video;         // server-side copy with the audio removed
    // Belt and braces: the file has no audio track, and the element is muted
    // anyway. A projector accidentally blasting a room is unrecoverable.
    video.muted = true;
    video.defaultMuted = true;
    video.volume = 0;
    video.setAttribute('muted', '');
    video.disableRemotePlayback = true;
    video.playsInline = true;
    video.autoplay = true;
    video.controls = false;
    video.poster = item.display;
    video.addEventListener('volumechange', function () {
      if (!video.muted || video.volume > 0) { video.muted = true; video.volume = 0; }
    });
    layer.appendChild(video);

    var moved = false;
    var go = function () { if (!moved) { moved = true; advance(); } };

    video.addEventListener('ended', go);
    video.addEventListener('error', go);

    // Videos play muted, so whatever was said is shown instead. Segments come
    // from the server already transcribed; this just follows the clock.
    var cues = item.captions || [];
    if (cues.length) {
      var shown = null;
      video.addEventListener('timeupdate', function () {
        var t = video.currentTime;
        var cue = null;
        for (var i = 0; i < cues.length; i++) {
          if (t >= cues[i].start - 0.15 && t <= cues[i].end + 0.35) { cue = cues[i]; break; }
        }
        var text = cue ? cue.text : '';
        if (text !== shown) {
          shown = text;
          subsEl.innerHTML = text ? '<span>' + escapeHtml(text) + '</span>' : '';
        }
      });
      video.addEventListener('ended', function () { subsEl.innerHTML = ''; });
    }

    // Budget: the clip's own length plus a beat, capped so one guest's
    // five-minute video can't hold the screen hostage.
    var budget = item.duration
      ? Math.min(MAX_VIDEO, item.duration * 1000 + 1200)
      : Math.min(MAX_VIDEO, DWELL * 2);

    video.play().catch(function () {
      // Autoplay refused. The poster frame is still up, so treat it as a
      // still photo rather than staring at it for the whole clip length.
      budget = DWELL;
      schedule(budget, go);
    });

    schedule(budget, go);
  }

  function preloadNext() {
    var peek = priority.length ? byId[priority[0]] : byId[playlist[0]];
    if (peek && peek.kind !== 'video') new Image().src = peek.display;
  }

  function paintCaption(item) {
    var who = [item.guest_name, tableLabel(item.table_id)]
      .filter(Boolean).join('  ·  ');

    var html = '';
    if (item.message) html += '<p class="msg">&ldquo;' + escapeHtml(item.message) + '&rdquo;</p>';
    if (who) html += '<div class="who">' + escapeHtml(who) + '</div>';
    attribEl.innerHTML = html;
    captionEl.classList.remove('hide');
    if (!showCaptions) captionEl.classList.add('hide');
  }

  function announce(item) {
    var name = item.guest_name || 'Someone';
    arrivalEl.textContent = 'Just added by ' + name +
      (item.table_id ? ' · ' + tableLabel(item.table_id) : '');
    arrivalEl.classList.add('show');
    setTimeout(function () { arrivalEl.classList.remove('show'); }, 5200);
    item.fresh = false;
  }

  function tableLabel(id) {
    if (!id) return '';
    return /^\d+$/.test(String(id)) ? 'Table ' + id : String(id);
  }

  function escapeHtml(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------------------------------------------------------------- timing */

  function schedule(ms, fn) {
    clearTimeout(timer);
    if (paused) return;
    timer = setTimeout(fn || advance, ms);
  }

  function advance() {
    if (paused) return;
    var item = nextItem();
    if (item) show(item);
    else schedule(4000);          // nothing yet; check back shortly
  }

  function togglePause() {
    paused = !paused;
    pausedEl.classList.toggle('on', paused);
    if (paused) {
      clearTimeout(timer);
      var video = layers[front].querySelector('video');
      if (video) video.pause();
    } else {
      var v = layers[front].querySelector('video');
      if (v) v.play().catch(function () {});
      advance();
    }
  }

  /* ----------------------------------------------------------------- feeds */

  function absorb(list, isNew) {
    var added = 0;
    list.forEach(function (item) {
      if (byId[item.id]) return;
      byId[item.id] = item;
      pool.push(item);
      head = Math.max(head, item.seq);
      if (isNew) priority.push(item.id);
      else playlist.push(item.id);
      added++;
    });
    return added;
  }

  function initialLoad() {
    fetch('/api/slideshow?limit=300')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        absorb(data.items, false);
        head = data.head || head;
        pool.forEach(function (i) { i.fresh = false; });  // no banner storm on boot
        refillPlaylist();
        if (pool.length) advance();
        else schedule(5000);
        backfill();
      })
      .catch(function () { schedule(8000, initialLoad); });
  }

  /* Page backwards in the background until we hold the whole evening. Without
     this the projector would loop the newest few hundred and quietly retire
     everything from the ceremony. */
  function backfill() {
    var oldest = pool.reduce(function (min, item) {
      return (min === 0 || item.seq < min) ? item.seq : min;
    }, 0);
    if (!oldest) return;

    fetch('/api/slideshow?before=' + oldest + '&limit=300')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.items.length) return;          // reached the beginning
        data.items.forEach(function (i) { i.fresh = false; });
        var added = absorb(data.items, false);
        if (added) setTimeout(backfill, 1500);   // gentle, so it never competes
      })
      .catch(function () {});
  }

  function poll() {
    fetch('/api/slideshow?after=' + head + '&limit=60')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var added = absorb(data.items, true);
        // A brand-new photo should reach the screen quickly, but not yank one
        // that's only just appeared.
        if (added && !paused && current) {
          var elapsed = DWELL;
          clearTimeout(timer);
          timer = setTimeout(advance, Math.min(2500, elapsed));
        }
      })
      .catch(function () {});
  }

  /* -------------------------------------------------------------- controls */

  document.addEventListener('keydown', function (e) {
    if (e.key === ' ') { e.preventDefault(); togglePause(); }
    if (e.key === 'ArrowRight') { clearTimeout(timer); paused = false; pausedEl.classList.remove('on'); advance(); }
    if (e.key === 'ArrowLeft') {
      // Step back by re-showing a random earlier item; a true history stack
      // isn't worth the complexity for a projector.
      clearTimeout(timer);
      var prev = pool[Math.floor(Math.random() * pool.length)];
      if (prev) show(prev);
    }
    if (e.key === 'c' || e.key === 'C') {
      showCaptions = !showCaptions;
      captionEl.classList.toggle('hide', !showCaptions);
    }
    if (e.key === 'q' || e.key === 'Q') {
      showPromo = !showPromo;
      promoEl.classList.toggle('hide', !showPromo);
    }
    if (e.key === 'f' || e.key === 'F') {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen().catch(function () {});
    }
  });

  var cursorTimer = null;
  document.addEventListener('mousemove', function () {
    document.body.classList.add('show-cursor');
    clearTimeout(cursorTimer);
    cursorTimer = setTimeout(function () {
      document.body.classList.remove('show-cursor');
    }, 2500);
  });

  /* Keep the projector's display awake. */
  function holdWakeLock() {
    if (!navigator.wakeLock) return;
    navigator.wakeLock.request('screen').then(function (lock) {
      lock.addEventListener('release', function () { setTimeout(holdWakeLock, 1000); });
    }).catch(function () {});
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) holdWakeLock();
  });

  /* ------------------------------------------------------------------ boot */

  holdWakeLock();
  initialLoad();
  setInterval(poll, POLL_MS);
})();
