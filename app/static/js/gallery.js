/* The album: an endlessly-scrolling wall of everything guests have sent,
   with a lightbox and a quiet poll so new arrivals appear on their own. */

(function () {
  var cfg = window.GALLERY || {};
  var grid = document.getElementById('grid');
  var empty = document.getElementById('empty');
  var statsEl = document.getElementById('stats');
  var notesEl = document.getElementById('notes');
  var sentinel = document.getElementById('sentinel');

  var items = [];          // newest first, matches DOM order
  var oldestSeq = 0;       // paging cursor going backwards
  var newestSeq = 0;       // polling cursor going forwards
  var exhausted = false;
  var loading = false;

  /* ------------------------------------------------------------ rendering */

  function tileFor(item) {
    var button = document.createElement('button');
    button.className = 'tile';
    button.setAttribute('aria-label', 'Open photo');

    var ratio = (item.width && item.height) ? (item.height / item.width) : 0.75;
    var img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.alt = item.guest_name ? ('Photo from ' + item.guest_name) : 'Wedding photo';
    // Reserve the right box before the image lands so the column doesn't jump.
    img.style.aspectRatio = (item.width && item.height) ? (item.width + '/' + item.height) : '4/3';
    img.src = item.thumb;
    button.appendChild(img);

    if (item.kind === 'video') {
      var play = document.createElement('div');
      play.className = 'play';
      play.innerHTML = '<svg width="38" height="38" viewBox="0 0 24 24" fill="#fff">' +
                       '<circle cx="12" cy="12" r="11" fill="rgba(0,0,0,.42)"/>' +
                       '<path d="M10 8.2l6 3.8-6 3.8z"/></svg>';
      button.appendChild(play);
    }

    if (item.guest_name || item.table_id) {
      var by = document.createElement('div');
      by.className = 'by';
      by.textContent = [item.guest_name, item.table_id ? 'Table ' + item.table_id : '']
        .filter(Boolean).join(' · ');
      button.appendChild(by);
    }

    button.addEventListener('click', function () { openLightbox(items.indexOf(item)); });
    return button;
  }

  function appendOlder(list) {
    list.forEach(function (item) {
      items.push(item);
      grid.appendChild(tileFor(item));
      oldestSeq = oldestSeq ? Math.min(oldestSeq, item.seq) : item.seq;
      newestSeq = Math.max(newestSeq, item.seq);
    });
    if (items.length) empty.classList.add('hidden');
  }

  function prependNewer(list) {
    // API gives newest first; insert so the newest ends up at the very top.
    list.slice().reverse().forEach(function (item) {
      items.unshift(item);
      grid.insertBefore(tileFor(item), grid.firstChild);
      newestSeq = Math.max(newestSeq, item.seq);
    });
    if (items.length) empty.classList.add('hidden');
  }

  /* -------------------------------------------------------------- loading */

  function loadOlder() {
    if (loading || exhausted) return;
    loading = true;
    var url = '/api/media?limit=40' + (oldestSeq ? '&before=' + oldestSeq : '');
    getJSON(url)
      .then(function (data) {
        if (!data.items.length) exhausted = true;
        appendOlder(data.items);
        loading = false;
      })
      .catch(function () { loading = false; });
  }

  function poll() {
    if (!newestSeq) return refreshStats();
    getJSON('/api/media?limit=40&after=' + newestSeq)
      .then(function (data) {
        if (data.items.length) {
          prependNewer(data.items);
          toast(data.items.length === 1 ? 'A new photo just arrived' :
                data.items.length + ' new photos just arrived');
        }
        refreshStats();
      })
      .catch(function () {});
  }

  function refreshStats() {
    getJSON('/api/stats').then(function (s) {
      var bits = [
        { n: s.photos, label: 'photos' },
        { n: s.videos, label: 'videos' },
        { n: s.contributors, label: 'contributors' },
        { n: s.notes, label: 'notes' }
      ].filter(function (b) { return b.n > 0; });
      statsEl.innerHTML = bits.map(function (b) {
        return '<div class="stat"><b>' + b.n + '</b><span>' + b.label + '</span></div>';
      }).join('');
    }).catch(function () {});
  }

  function loadNotes() {
    getJSON('/api/notes?limit=8').then(function (data) {
      if (!data.items.length) return;
      notesEl.innerHTML = data.items.map(function (n) {
        var who = [n.guest_name, n.table_id ? 'Table ' + n.table_id : '']
          .filter(Boolean).join(' · ') || 'Anonymous';
        return '<div class="note"><p>' + esc(n.message) + '</p>' +
               '<div class="who">— ' + esc(who) + '</div></div>';
      }).join('');
    }).catch(function () {});
  }

  /* ------------------------------------------------------------- lightbox */

  var lb = document.getElementById('lightbox');
  var lbStage = document.getElementById('lbStage');
  var lbCap = document.getElementById('lbCap');
  var current = -1;

  function openLightbox(index) {
    if (index < 0 || index >= items.length) return;
    current = index;
    var item = items[index];

    lbStage.innerHTML = '';
    if (item.kind === 'video') {
      var video = document.createElement('video');
      video.src = item.video;
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      lbStage.appendChild(video);
    } else {
      var img = document.createElement('img');
      img.src = item.display;
      img.alt = '';
      lbStage.appendChild(img);
    }

    var who = [item.guest_name, item.table_id ? 'Table ' + item.table_id : '']
      .filter(Boolean).join(' · ');
    var caption = [item.message, who].filter(Boolean).join(' — ');
    if (cfg.allowDownload) {
      caption += (caption ? '  ·  ' : '') +
        '<a style="color:#fff" href="/m/' + item.id + '/original" download>Download</a>';
    }
    lbCap.innerHTML = caption;

    lb.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  function closeLightbox() {
    lb.classList.remove('open');
    lbStage.innerHTML = '';        // stops any playing video
    document.body.style.overflow = '';
    current = -1;
  }

  function step(delta) {
    var next = current + delta;
    if (next < 0 || next >= items.length) return;
    // Reached the end of what's loaded? Pull more in before stepping.
    if (next >= items.length - 3) loadOlder();
    openLightbox(next);
  }

  document.getElementById('lbClose').addEventListener('click', closeLightbox);
  document.getElementById('lbPrev').addEventListener('click', function () { step(-1); });
  document.getElementById('lbNext').addEventListener('click', function () { step(1); });
  lb.addEventListener('click', function (e) { if (e.target === lb) closeLightbox(); });

  document.addEventListener('keydown', function (e) {
    if (!lb.classList.contains('open')) return;
    if (e.key === 'Escape') closeLightbox();
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
  });

  // Swipe between photos on a phone.
  var touchX = null;
  lb.addEventListener('touchstart', function (e) { touchX = e.touches[0].clientX; }, { passive: true });
  lb.addEventListener('touchend', function (e) {
    if (touchX === null) return;
    var dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 60) step(dx > 0 ? -1 : 1);
    touchX = null;
  }, { passive: true });

  /* ----------------------------------------------------------------- boot */

  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) loadOlder();
    }, { rootMargin: '600px' }).observe(sentinel);
  } else {
    window.addEventListener('scroll', function () {
      if (window.innerHeight + window.scrollY > document.body.offsetHeight - 600) loadOlder();
    });
  }

  loadOlder();
  loadNotes();
  refreshStats();
  setInterval(poll, 20000);
  setInterval(loadNotes, 120000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) poll();
  });
})();
