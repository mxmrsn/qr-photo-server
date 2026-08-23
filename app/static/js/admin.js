/* Album management: approve, hide, delete, download, and keep an eye on
   anything that failed to process. */

(function () {
  var grid = document.getElementById('agrid');
  var kpi = document.getElementById('kpi');
  var emptyEl = document.getElementById('empty');
  var selCount = document.getElementById('selCount');
  var notesEl = document.getElementById('notes');
  var sentinel = document.getElementById('sentinel');

  var filter = 'all';
  var items = [];
  var selected = {};
  var oldest = 0;
  var exhausted = false;
  var loading = false;

  /* ------------------------------------------------------------------ load */

  function load(reset) {
    if (loading) return;
    if (reset) { items = []; oldest = 0; exhausted = false; grid.innerHTML = ''; selected = {}; sync(); }
    if (exhausted) return;
    loading = true;
    var url = '/api/admin/media?filter=' + filter + '&limit=80' + (oldest ? '&before=' + oldest : '');
    getJSON(url).then(function (data) {
      loading = false;
      if (!data.items.length) exhausted = true;
      data.items.forEach(function (item) {
        items.push(item);
        oldest = oldest ? Math.min(oldest, item.seq) : item.seq;
        grid.appendChild(card(item));
      });
      emptyEl.classList.toggle('hidden', items.length > 0);
      paintKpi(data.stats);
    }).catch(function (err) {
      loading = false;
      toast(err.message);
    });
  }

  function paintKpi(st) {
    if (!st) return;
    var cells = [
      ['total', 'items'], ['images', 'photos'], ['videos', 'videos'],
      ['contributors', 'guests'], ['pending', 'awaiting'], ['processing', 'processing'],
      ['failed', 'failed'], ['hidden', 'hidden']
    ];
    kpi.innerHTML = cells.map(function (c) {
      return '<div><b>' + (st[c[0]] || 0) + '</b><span>' + c[1] + '</span></div>';
    }).join('') + '<div><b>' + humanBytes(st.bytes || 0) + '</b><span>stored</span></div>';
  }

  /* --------------------------------------------------------------- one card */

  function card(item) {
    var el = document.createElement('div');
    el.className = 'acard';
    el.dataset.id = item.id;

    var badges = [];
    if (item.kind === 'video') badges.push('<span class="badge">video</span>');
    if (item.status === 'processing') badges.push('<span class="badge">working…</span>');
    if (item.status === 'failed') badges.push('<span class="badge warn">failed</span>');
    if (item.hidden) badges.push('<span class="badge warn">hidden</span>');
    if (!item.approved && item.status === 'ready') badges.push('<span class="badge">pending</span>');
    if (item.featured) badges.push('<span class="badge ok">featured</span>');

    var who = esc(item.guest_name || 'Anonymous');
    var meta = [
      esc(tableLabel(item.table_id)),
      humanBytes(item.bytes),
      timeAgo(item.uploaded_at)
    ].filter(Boolean).join(' · ');

    var preview = item.status === 'ready'
      ? '<img src="' + item.thumb + '" alt="" loading="lazy">'
      : '<div style="aspect-ratio:1;display:grid;place-items:center;font-size:11px;' +
        'opacity:.6;text-align:center;padding:10px">' +
        esc(item.error || 'processing…') + '</div>';

    el.innerHTML =
      preview +
      '<div class="badges">' + badges.join('') + '</div>' +
      '<div class="pick">✓</div>' +
      '<div class="info"><div class="g">' + who + '</div><div class="m">' + meta + '</div></div>' +
      '<div class="row">' +
        (item.hidden ? '<button data-a="show">Unhide</button>' : '<button data-a="hide">Hide</button>') +
        (item.approved ? '' : '<button data-a="approve">Approve</button>') +
        (item.featured ? '<button data-a="unfeature">Unstar</button>' : '<button data-a="feature">Star</button>') +
        (item.status === 'failed' ? '<button data-a="reprocess">Retry</button>' : '') +
        '<button data-a="delete" class="danger">Delete</button>' +
      '</div>';

    el.querySelector('.pick').addEventListener('click', function (e) {
      e.stopPropagation();
      if (selected[item.id]) delete selected[item.id]; else selected[item.id] = true;
      el.classList.toggle('sel', !!selected[item.id]);
      sync();
    });

    var img = el.querySelector('img');
    if (img) img.addEventListener('click', function () { openLightbox(item); });

    el.querySelectorAll('.row button').forEach(function (btn) {
      btn.addEventListener('click', function () { act([item.id], btn.dataset.a); });
    });

    return el;
  }

  function sync() {
    var n = Object.keys(selected).length;
    selCount.textContent = n ? n + ' selected' : '';
  }

  /* ---------------------------------------------------------------- actions */

  function act(ids, action) {
    if (!ids.length) { toast('Select something first'); return; }
    if (action === 'delete' && !confirm(
      'Permanently delete ' + ids.length + ' item' + (ids.length > 1 ? 's' : '') +
      '? The original files go too.')) return;

    postJSON('/api/admin/action', { ids: ids, action: action })
      .then(function () {
        ids.forEach(function (id) { delete selected[id]; });
        toast('Done');
        load(true);
      })
      .catch(function (err) { toast(err.message); });
  }

  document.getElementById('bulkApprove').addEventListener('click', function () {
    act(Object.keys(selected), 'approve');
  });
  document.getElementById('bulkHide').addEventListener('click', function () {
    act(Object.keys(selected), 'hide');
  });
  document.getElementById('bulkDelete').addEventListener('click', function () {
    act(Object.keys(selected), 'delete');
  });

  document.getElementById('filters').addEventListener('click', function (e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    document.querySelectorAll('#filters button').forEach(function (b) { b.classList.remove('on'); });
    btn.classList.add('on');
    filter = btn.dataset.f;
    load(true);
  });

  /* ------------------------------------------------------------------ notes */

  function loadNotes() {
    getJSON('/api/admin/notes').then(function (data) {
      if (!data.items.length) { notesEl.innerHTML = '<p style="opacity:.5">No notes yet.</p>'; return; }
      notesEl.innerHTML = data.items.map(function (n) {
        var who = [n.guest_name, tableLabel(n.table_id)]
          .filter(Boolean).join(' · ') || 'Anonymous';
        return '<div class="note"' + (n.hidden ? ' style="opacity:.45"' : '') + '>' +
          '<p>' + esc(n.message) + '</p>' +
          '<div class="who">— ' + esc(who) + ' · ' + timeAgo(n.created_at) +
          ' <button class="btn small ghost" data-note="' + n.id + '" data-a="' +
          (n.hidden ? 'show' : 'hide') + '">' + (n.hidden ? 'Unhide' : 'Hide') + '</button>' +
          ' <button class="btn small ghost" data-note="' + n.id + '" data-a="delete">Delete</button>' +
          '</div></div>';
      }).join('');
    }).catch(function () {});
  }

  notesEl.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-note]');
    if (!btn) return;
    if (btn.dataset.a === 'delete' && !confirm('Delete this note?')) return;
    postJSON('/api/admin/note-action', { id: btn.dataset.note, action: btn.dataset.a })
      .then(loadNotes)
      .catch(function (err) { toast(err.message); });
  });

  /* --------------------------------------------------------------- lightbox */

  var lb = document.getElementById('lightbox');
  var lbStage = document.getElementById('lbStage');
  var lbCap = document.getElementById('lbCap');

  function openLightbox(item) {
    lbStage.innerHTML = item.kind === 'video'
      ? '<video src="' + item.video + '" controls autoplay playsinline></video>'
      : '<img src="' + item.display + '" alt="">';
    lbCap.innerHTML = esc(item.original_name || '') + ' · ' +
      esc(item.guest_name || 'Anonymous') +
      ' · <a style="color:#fff" href="' + item.original + '" download>original</a>';
    lb.classList.add('open');
  }

  document.getElementById('lbClose').addEventListener('click', function () {
    lb.classList.remove('open');
    lbStage.innerHTML = '';
  });
  lb.addEventListener('click', function (e) {
    if (e.target === lb) { lb.classList.remove('open'); lbStage.innerHTML = ''; }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { lb.classList.remove('open'); lbStage.innerHTML = ''; }
  });

  /* ------------------------------------------------------------------- boot */

  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) load(false);
    }, { rootMargin: '500px' }).observe(sentinel);
  }

  load(true);
  loadNotes();
  setInterval(function () {
    // Keep the "processing" badges honest without disturbing a scroll position.
    getJSON('/api/admin/media?filter=' + filter + '&limit=1')
      .then(function (d) { paintKpi(d.stats); })
      .catch(function () {});
  }, 15000);
})();
