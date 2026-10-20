/* music-ears 黑胶播放器（从作者私用的聊天前端里拆出来的，2026-10-19）。
 *
 *   <link rel="stylesheet" href="vinyl.css">
 *   <script src="vinyl.js"></script>
 *   <script>const vn = MusicEarsVinyl.mount({ base: '', open: true });</script>
 *
 * 选项：
 *   base        music-ears serve 的地址，结尾带 /（同一个网站就留空）
 *   open        一打开就显示播放页（独立页面用）；false 时只在 vn.open() 后出现
 *   mini        缩小后留一个悬浮小唱片（默认 true）
 *   storageKey  队列、放到哪存在浏览器里用的键
 *   onListen(track)  自己接"一起听"（比如你的后端要先把乐谱卡递给 AI）：返回 Promise，resolve 后开始放。
 *                    不给就用 music-ears serve 自己的 /api/listen（拆好卡就开始放，AI 读 /api/now 跟进度）
 *
 * 音频由浏览器直接从网易云 CDN 取，地址由后端用你的网易云登录要来。
 * 图标来自 Lucide（ISC 许可证）。
 */
(function () {
  'use strict';
  const ICONS = {
    chevD: '<path d="m6 9 6 6 6-6"/>',
    list: '<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>',
    play: '<polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/>',
    pause: '<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>',
    prev: '<polygon points="19 20 9 12 19 4 19 20" fill="currentColor"/><line x1="5" x2="5" y1="19" y2="5"/>',
    next: '<polygon points="5 4 15 12 5 20 5 4" fill="currentColor"/><line x1="19" x2="19" y1="5" y2="19"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    headphones: '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>',
    dots: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  };
  const ic = (n) => '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICONS[n] + '</svg>';
  const fmt = (t) => { t = Math.max(0, t || 0) | 0; return (t / 60 | 0) + ':' + String(t % 60).padStart(2, '0'); };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function parseLrc(raw, traw) {
    const parse = (r) => {
      const out = [];
      for (const ln of String(r || '').split('\n')) {
        const text = ln.replace(/\[[^\]]*\]/g, '').trim();
        for (const m of ln.matchAll(/\[(\d+):(\d+(?:\.\d+)?)\]/g)) out.push({ t: +m[1] * 60 + +m[2], text });
      }
      return out.sort((a, b) => a.t - b.t);
    };
    const lines = parse(raw).filter(l => l.text && !/^[^：:]{1,12}[：:]/.test(l.text));   // 去掉"作词：xx"这类
    const tr = parse(traw);
    for (const l of lines) { const m = tr.find(x => Math.abs(x.t - l.t) < 0.3); if (m && m.text) l.zh = m.text; }
    return lines;
  }

  function mount(opts) {
    opts = Object.assign({ base: '', open: false, mini: true, storageKey: 'mev-vinyl', onListen: null }, opts || {});
    const B = opts.base;
    const audio = new Audio(); audio.preload = 'auto';
    const S = { queue: [], idx: -1, lrc: [], tab: 'pl', plStack: [], curLine: -1, showN: 300 };

    const root = document.createElement('div');
    root.className = 'mev';
    root.innerHTML =
      '<div class="mev-sheet off">' +
        '<div class="mev-bg"></div>' +
        '<div class="mev-head">' +
          '<button class="mev-hbtn" data-a="min" title="缩小">' + ic('chevD') + '</button>' +
          '<div class="mev-ht"><b data-r="name">还没放歌</b><span data-r="artist">点右上角挑一首</span></div>' +
          '<button class="mev-hbtn" data-a="lib" title="歌单">' + ic('list') + '</button>' +
        '</div>' +
        '<div class="mev-body" data-r="body">' +
          '<div class="mev-main" data-r="main">' +
            '<div class="mev-stage"><div class="mev-disc" data-r="disc"><img class="mev-cover" alt=""></div><i class="mev-arm" data-r="arm"></i></div>' +
            '<div class="mev-lyric" data-r="lyric"></div>' +
          '</div>' +
          '<div class="mev-bar"><span data-r="cur">0:00</span><input type="range" min="0" max="1000" value="0" data-r="seek" aria-label="进度"><span data-r="dur">0:00</span></div>' +
          '<div class="mev-ctl">' +
            '<button data-a="prev" title="上一首">' + ic('prev') + '</button>' +
            '<button class="big" data-a="play" title="播放">' + ic('play') + '</button>' +
            '<button data-a="next" title="下一首">' + ic('next') + '</button>' +
          '</div>' +
        '</div>' +
        '<div class="mev-lib" data-r="lib" hidden>' +
          '<div class="mev-tabs"><button data-t="pl" class="on">歌单</button><button data-t="q">搜索</button><button data-t="now">正在放</button></div>' +
          '<label class="mev-search" data-r="searchBox" hidden>' + ic('search') +
            '<input type="search" enterkeyhint="search" placeholder="搜歌名、歌手…" autocomplete="off" data-r="search"></label>' +
          '<div class="mev-list" data-r="list"></div>' +
        '</div>' +
        '<div class="mev-login" data-r="login" hidden><div class="box">' +
          '<h3>网易云扫码登录</h3><div class="qr" data-r="qr"></div><p data-r="lstate"></p>' +
          '<p>同一部手机扫不了自己：截图这个二维码，在网易云"扫一扫"里从相册选；或者点下面在网易云里打开。登录信息只存在跑 music-ears 的那台电脑上。</p>' +
          '<div class="row"><a data-r="lopen" target="_blank" rel="noopener">在网易云里打开</a><button data-a="lclose">关掉</button></div>' +
        '</div></div>' +
      '</div>' +
      '<div class="mev-mini" hidden><div class="mev-disc"><img class="mev-cover" alt="" draggable="false"></div>' +
        '<button class="x" title="关掉">' + ic('x') + '</button><button class="l" title="一起听">' + ic('headphones') + '</button></div>' +
      '<div class="mev-toast"></div><button class="mev-chip" hidden></button>';
    document.body.appendChild(root);
    const $ = (r) => root.querySelector('[data-r="' + r + '"]');
    const sheet = root.querySelector('.mev-sheet'), mini = root.querySelector('.mev-mini'), toastEl = root.querySelector('.mev-toast'), chip = root.querySelector('.mev-chip');
    chip.onclick = () => open();

    let toastT = 0;
    function toast(msg, ms) {
      toastEl.textContent = msg; toastEl.classList.add('on');
      clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('on'), ms || 2800);
    }
    async function api(op, arg) {
      const r = await fetch(B + 'api/vinyl?op=' + op + (arg != null ? '&arg=' + encodeURIComponent(arg) : ''), { cache: 'no-store', credentials: 'include' });
      return r.json();
    }
    const img = (u, size) => u ? B + 'cover?u=' + encodeURIComponent(u) + '&s=' + size : '';
    const isOpen = () => !sheet.classList.contains('off');

    function save() {
      try { localStorage.setItem(opts.storageKey, JSON.stringify({ queue: S.queue.slice(0, 3000), idx: S.idx, pos: audio.currentTime || S.resume || 0, closed: !!S.closed })); } catch (e) {}
    }
    function load() {
      try { const d = JSON.parse(localStorage.getItem(opts.storageKey) || 'null'); if (d && d.queue) { S.queue = d.queue; S.idx = d.idx; S.resume = d.pos || 0; S.closed = !!d.closed; } } catch (e) {}
    }
    function paintMini() { mini.hidden = !opts.mini || !S.queue[S.idx] || S.closed || isOpen(); }

    function paintSong() {
      const t = S.queue[S.idx];
      $('name').textContent = t ? t.name : '还没放歌';
      $('artist').textContent = t ? (t.artists || []).join(' / ') + (t.album ? ' · ' + t.album : '') : '点右上角挑一首';
      root.querySelectorAll('.mev-cover').forEach(im => { if (t && t.cover) im.src = img(t.cover, 500); else im.removeAttribute('src'); });
      root.querySelector('.mev-bg').style.backgroundImage = t && t.cover ? 'url("' + img(t.cover, 200) + '")' : '';
      paintMini();
      if (t && 'mediaSession' in navigator) {
        try {
          navigator.mediaSession.metadata = new MediaMetadata({ title: t.name, artist: (t.artists || []).join(' / '), album: t.album || '',
            artwork: t.cover ? [{ src: new URL(img(t.cover, 512), location.href).href, sizes: '512x512', type: 'image/jpeg' }] : [] });
        } catch (e) {}
      }
    }
    function paintPlay() {
      const on = !audio.paused;
      root.querySelectorAll('.mev-disc').forEach(d => d.classList.toggle('on', on));
      $('arm').classList.toggle('on', on);
      root.querySelector('[data-a="play"]').innerHTML = ic(on ? 'pause' : 'play');
      if ('mediaSession' in navigator) try { navigator.mediaSession.playbackState = on ? 'playing' : 'paused'; } catch (e) {}
    }
    function paintLyric() {
      const box = $('lyric'); box.innerHTML = '';
      const inner = document.createElement('div'); inner.className = 'in';
      if (!S.lrc.length) { const d = document.createElement('div'); d.className = 'ln cur'; d.textContent = S.queue[S.idx] ? '（这首没有歌词）' : ''; inner.appendChild(d); }
      for (const l of S.lrc) {
        const d = document.createElement('div'); d.className = 'ln'; d.textContent = l.text;
        if (l.zh) { const z = document.createElement('small'); z.textContent = l.zh; d.appendChild(z); }
        inner.appendChild(d);
      }
      box.appendChild(inner); box.scrollTop = 0;
      S.curLine = -1; tick();
    }
    function lyricFollow(smooth) {
      const box = $('lyric'), inner = box.querySelector('.in'); if (!inner) return;
      const el = inner.children[Math.max(0, S.curLine)]; if (!el) return;
      box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.offsetHeight / 2, behavior: smooth ? 'smooth' : 'auto' });
    }
    function tick() {
      const t = audio.currentTime || 0, d = audio.duration || (S.queue[S.idx] || {}).dur || 0, seek = $('seek');
      $('cur').textContent = fmt(t); $('dur').textContent = fmt(d);
      if (!S.seeking && d) seek.value = Math.round(t / d * 1000);
      seek.style.setProperty('--p', (seek.value / 10) + '%');
      let i = -1; for (let k = 0; k < S.lrc.length; k++) { if (S.lrc[k].t <= t + 0.25) i = k; else break; }
      const free = !S.lyrTouch && !(S.lyrHold > Date.now());
      if (i !== S.curLine) {
        S.curLine = i; report();
        const inner = $('lyric').querySelector('.in'); if (!inner) return;
        [...inner.children].forEach((c, k) => c.classList.toggle('cur', k === i));
        if (free) lyricFollow(true);
      } else if (free && S.lyrHold) { S.lyrHold = 0; lyricFollow(true); }   // 手滑过、5 秒到了：滚回来
    }

    async function playAt(i, startAt) {
      if (i < 0 || i >= S.queue.length) return;
      if (S.together && S.queue[i].id !== S.together.id) togetherEnd();   // 换到别的歌：这首的一起听收尾
      S.idx = i; S.lrc = []; S.closed = false; paintSong(); paintLyric(); save();
      const t = S.queue[i], my = (S.gen = (S.gen || 0) + 1);
      $('artist').textContent = '取音频…';
      const d = await api('url', t.id).catch(() => ({}));
      if (my !== S.gen) return;
      if (!d.ok || !d.src || !d.src.url) {
        toast('《' + t.name + '》放不了，跳下一首');
        S.skips = (S.skips || 0) + 1; if (S.skips < 5) playAt(i + 1); return;
      }
      S.skips = 0; paintSong();
      audio.src = d.src.url;
      if (startAt) audio.currentTime = startAt;
      audio.play().catch(() => {});
      api('lyric', t.id).then(l => { if (my !== S.gen || !l.ok) return; S.lrc = parseLrc(l.lrc, l.tlyric); paintLyric(); }).catch(() => {});
    }
    function toggle() {
      if (S.idx < 0 || !S.queue[S.idx]) return showLib(true);
      if (!audio.src) return playAt(S.idx, S.resume || 0);
      if (audio.paused) audio.play().catch(() => playAt(S.idx, audio.currentTime)); else audio.pause();
    }

    /* 一起听：拆过的直接放；没拆过的先拆（几分钟），拆好再放。放的时候把位置报给后端，AI 读 /api/now 就知道放到哪 */
    /* 一起听：要先有乐谱卡。没拆过就问要不要先拆（不动正在放的），拆好了再点一起听。
       有卡：直接开始放，放的时候把位置报给后端，AI 读 /api/now 跟进度 */
    async function listen(t) {
      if (!t || !t.id) return;
      const cur = S.queue[S.idx], same = cur && cur.id === t.id;
      const resume = same ? (audio.currentTime || S.resume || 0) : 0;
      if (!opts.onListen) {
        let d;
        try {
          d = await (await fetch(B + 'api/listen', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: t.id, name: t.name, artists: t.artists || [] }) })).json();
        } catch (e) { return toast('一起听没开起来：' + e.message); }
        if (d.need_card) {
          if (d.making) { mkTrack(t.id); return toast('《' + t.name + '》的乐谱卡' + (d.making === 'making' ? '正在拆' : '在排队拆') + '，好了提醒你'); }
          const mins = Math.max(4, Math.round((t.dur || 240) * 2.1 / 60));
          if (await ask('《' + t.name + '》还没拆乐谱卡', '一起听要先把乐谱卡给 AI。这首大约要 ' + mins + ' 分钟，拆的时候上方有进度，好了提醒你，再点一起听。', '现在拆')) makeCard(t);
          return;
        }
        if (!d.ok) return toast('一起听没开起来' + (d.error ? '：' + d.error : ''));
      }
      audio.pause();
      if (!same) {
        const old = S.queue.findIndex(x => x.id === t.id);
        if (old >= 0) S.idx = old;
        else if (S.idx < 0 || !cur) { S.queue = [t]; S.idx = 0; }
        else { S.queue.splice(S.idx + 1, 0, t); S.idx++; }
        audio.removeAttribute('src'); S.lrc = []; S.closed = false; paintSong(); paintLyric(); save();
      }
      const tg = S.together = { id: t.id, name: t.name, resume, started: false };
      const go = () => { if (S.together !== tg) return; tg.started = true; if (audio.src && !audio.paused) return report(true); playAt(S.idx, tg.resume); };
      if (opts.onListen) {   // 接入方自己处理（比如先把卡递给 AI）
        $('artist').textContent = '一起听 · 准备中…';
        try { await opts.onListen(t); go(); } catch (e) { S.together = null; toast('一起听没接上' + (e && e.message ? '：' + e.message : '') + '，先自己放着'); playAt(S.idx, resume); }
        return;
      }
      toast('开始一起听《' + t.name + '》');
      go();
    }

    /* 拆乐谱卡：只拆、存着；后端一次拆一首。进度条浮在上方（不管播放页开没开），在等的编号记在浏览器里 */
    async function makeCard(t) {
      let d;
      try {
        d = await (await fetch(B + 'api/make', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: t.id, name: t.name, dur: t.dur || 0 }) })).json();
      } catch (e) { return toast('没排上：' + e.message); }
      if (!d.ok) return toast('没排上' + (d.error ? '：' + d.error : ''));
      if (d.have) return toast('《' + t.name + '》拆过了，点「一起听」就行');
      if (d.ahead < 0) { mkTrack(t.id); return toast('《' + t.name + '》已经在拆或者排着了'); }
      toast(d.ahead ? '《' + t.name + '》排上了，前面还有 ' + d.ahead + ' 首' : '开始拆《' + t.name + '》，好了提醒你', 4000);
      mkTrack(t.id);
    }
    const MK = opts.storageKey + '-making';
    let mkT = 0;
    const mkGet = () => { try { return new Set(JSON.parse(localStorage.getItem(MK) || '[]')); } catch (e) { return new Set(); } };
    const mkPut = (m) => { try { localStorage.setItem(MK, JSON.stringify([...m])); } catch (e) {} };
    function mkTrack(sid) { const m = mkGet(); m.add(sid); mkPut(m); mkPoll(0); }
    function mkPoll(delay) {
      clearTimeout(mkT);
      mkT = setTimeout(async () => {
        const mine = mkGet();
        let d = null;
        try { d = await (await fetch(B + 'api/queue', { cache: 'no-store', credentials: 'include' })).json(); } catch (e) {}
        if (!d || !d.ok) return mine.size && mkPoll(15000);
        for (const x of d.done || []) if (mine.has(x.sid)) { mine.delete(x.sid); toast(x.ok ? '《' + x.title + '》的乐谱卡拆好了，可以一起听了' : '《' + x.title + '》没拆成', 6000); }
        const m = d.making, q = d.queued || [];
        for (const sid of [...mine]) if (!(m && m.sid === sid) && !q.some(x => x.sid === sid)) mine.delete(sid);   // 后端重启后队列没了
        mkPut(mine);
        if (m) {
          chip.textContent = '拆乐谱卡《' + m.name + '》· ' + (m.elapsed < m.est ? '约 ' + Math.max(1, Math.floor(m.elapsed / 60)) + '/' + Math.max(1, Math.round(m.est / 60)) + ' 分钟' : '快好了') + (q.length ? ' · 还排着 ' + q.length + ' 首' : '');
          chip.style.setProperty('--p', Math.min(100, Math.round(m.elapsed / m.est * 100)) + '%');
        } else if (q.length) chip.textContent = '乐谱卡排着 ' + q.length + ' 首';
        chip.hidden = !m && !q.length;
        if (!chip.hidden || mine.size) mkPoll(10000);
      }, delay == null ? 10000 : delay);
    }
    document.addEventListener('visibilitychange', () => { if (!document.hidden) mkPoll(0); });

    /* 问一句（是 / 否），用的是登录框那套样子 */
    function ask(title, text, okText) {
      return new Promise((done) => {
        const w = document.createElement('div'); w.className = 'mev-login mev-ask';
        w.innerHTML = '<div class="box"><h3></h3><p></p><div class="row"><button class="ok"></button><button class="no">取消</button></div></div>';
        w.querySelector('h3').textContent = title; w.querySelector('p').textContent = text; w.querySelector('.ok').textContent = okText || '好';
        const close = (v) => { w.remove(); done(v); };
        w.querySelector('.ok').onclick = () => close(true); w.querySelector('.no').onclick = () => close(false);
        w.onclick = (e) => { if (e.target === w) close(false); };
        root.appendChild(w);
      });
    }

    let repAt = 0;
    // 在放歌就报位置，不看页面记不记得"一起听"（手机切出去回来页面会重载，记的就丢了）；是不是一起听的那首由后端判断
    function report(force) {
      const t = S.queue[S.idx];
      if (!t || !t.id || !audio.src) return;
      if (!force && Date.now() - repAt < 4000) return;
      repAt = Date.now();
      const l = S.lrc[S.curLine];
      fetch(B + 'api/pos', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: t.id, pos: audio.currentTime || 0, dur: audio.duration || t.dur || 0, line: l ? l.text : '', playing: !audio.paused }) }).catch(() => {});
    }
    function togetherEnd(ended) {
      const tg = S.together; if (!tg) return;
      S.together = null;
      fetch(B + 'api/pos', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: tg.id, [ended ? 'ended' : 'stop']: true }) }).catch(() => {});
    }

    /* 下一首播放 / 加到末尾 / 从列表移除 */
    function queueOp(t, how, k) {
      if (how === 'rm') {
        S.queue.splice(k, 1);
        if (k < S.idx) S.idx--;
        else if (k === S.idx) { if (S.queue[S.idx]) playAt(S.idx); else { audio.pause(); S.idx = -1; paintSong(); } }
        save(); return renderLib();
      }
      if (S.idx < 0 || !S.queue[S.idx]) { S.queue = [t]; playAt(0); return; }
      if (how === 'next') {
        const old = S.queue.findIndex((x, i) => x.id === t.id && i !== S.idx);
        if (old >= 0) { S.queue.splice(old, 1); if (old < S.idx) S.idx--; }
        S.queue.splice(S.idx + 1, 0, t); toast('下一首放《' + t.name + '》');
      } else { S.queue.push(t); toast('《' + t.name + '》排到最后了'); }
      save();
    }
    function menu(anchor, items) {
      return new Promise((done) => {
        const m = document.createElement('div'); m.className = 'mev-menu';
        for (const it of items) { const b = document.createElement('button'); b.textContent = it.name; b.onclick = (e) => { e.stopPropagation(); close(it.id); }; m.appendChild(b); }
        root.appendChild(m);
        const r = anchor.getBoundingClientRect(), h = m.offsetHeight, w = m.offsetWidth;
        m.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w - 8)) + 'px';
        m.style.top = (r.bottom + h + 8 < innerHeight ? r.bottom - 4 : Math.max(8, r.top - h + 4)) + 'px';
        const away = (e) => { if (!m.contains(e.target)) close(null); };
        setTimeout(() => document.addEventListener('pointerdown', away, true), 0);
        function close(v) { document.removeEventListener('pointerdown', away, true); m.remove(); done(v); }
      });
    }
    function item(cover, title, sub, cur, onclick, more) {
      const b = document.createElement('button'); b.className = 'mev-item' + (cur ? ' cur' : '');
      const im = document.createElement('img'); if (cover) im.src = img(cover, 100); im.loading = 'lazy'; im.alt = '';
      const t = document.createElement('div'); t.className = 't';
      t.innerHTML = '<b>' + esc(title) + '</b><span>' + esc(sub) + '</span>';
      b.append(im, t); b.onclick = onclick;
      if (more) {
        const m = document.createElement('span'); m.className = 'mev-more'; m.innerHTML = ic('dots');
        m.onclick = (e) => { e.stopPropagation(); more(b); };
        b.appendChild(m);
      }
      return b;
    }
    function listMsg(html) { const l = $('list'); l.innerHTML = '<div class="mev-note">' + html + '</div>'; }
    function songs(list, label) {
      const l = $('list'); l.innerHTML = '';
      if (label) l.appendChild(item('', '← ' + label, '共 ' + list.length + ' 首，点一首从这里开始放', false, () => { S.plStack = []; renderLib(); }));
      const n = Math.min(list.length, S.showN), isNow = list === S.queue;
      for (let k = 0; k < n; k++) {
        const t = list[k];
        l.appendChild(item(t.cover, t.name, (t.artists || []).join(' / ') + ' · ' + fmt(t.dur),
          isNow ? k === S.idx : S.queue[S.idx] && S.queue[S.idx].id === t.id,
          () => { if (!isNow) S.queue = list.slice(); playAt(k); showLib(false); },
          (row) => menu(row, (isNow ? [{ id: 'next', name: '下一首播放' }, { id: 'rm', name: '从列表移除' }]
                                    : [{ id: 'next', name: '下一首播放' }, { id: 'end', name: '加到列表末尾' }]).concat([{ id: 'listen', name: '一起听' }, { id: 'card', name: '拆乐谱卡' }]))
            .then(how => { if (how === 'listen') { listen(t); showLib(false); } else if (how === 'card') makeCard(t); else if (how) queueOp(t, how, k); })));
      }
      if (list.length > n) l.appendChild(item('', '再显示 300 首', '还有 ' + (list.length - n) + ' 首', false, () => { S.showN = n + 300; songs(list, label); }));
    }
    async function renderLib() {
      if (S.tab === 'now') { S.showN = 300; return S.queue.length ? songs(S.queue) : listMsg('还没有在放的'); }
      if (S.tab === 'q') {
        const q = $('search').value.trim(); if (!q) return listMsg('输入歌名、歌手，回车搜');
        listMsg('搜…'); const d = await api('search', q).catch(() => ({})); S.showN = 300;
        return d.ok ? songs(d.songs || []) : listMsg(esc(d.error || '没搜到'));
      }
      const top = S.plStack[0];
      if (top) {
        listMsg('取《' + esc(top.name) + '》…'); const d = await api('tracks', top.id).catch(() => ({})); S.showN = 300;
        return d.ok ? songs(d.tracks || [], top.name) : listMsg(esc(d.error || '没取到'));
      }
      listMsg('取歌单…');
      const d = await api('playlists').catch(() => ({}));
      if (!d.ok || !d.playlists) {
        listMsg('还没登录网易云，看不到你的歌单。<button data-a="login">扫码登录</button>（不登录也能用"搜索"放免费的歌）');
        return;
      }
      const l = $('list'); l.innerHTML = '';
      for (const p of d.playlists) l.appendChild(item(p.cover, p.name, p.count + ' 首' + (p.mine ? '' : ' · 收藏的'), false, () => { S.plStack = [p]; renderLib(); }));
      l.appendChild(Object.assign(document.createElement('div'), { className: 'mev-note', innerHTML: '<button data-a="login">换个网易云账号</button>' }));
    }
    function showLib(on) {
      $('lib').hidden = !on; $('body').hidden = on;
      if (on) renderLib(); else { S.curLine = -1; tick(); }
    }

    /* 网易云扫码登录：二维码在页面上画（qrcode.min.js），每 2 秒问一次 */
    let lTimer = 0;
    function qrLib() {
      return window.qrcode ? Promise.resolve() : new Promise((ok, bad) => {
        const sc = document.createElement('script'); sc.src = B + 'qrcode.min.js'; sc.onload = ok; sc.onerror = bad; document.head.appendChild(sc);
      });
    }
    async function login() {
      const box = $('login'), st = $('lstate'), qr = $('qr'), op = $('lopen');
      box.hidden = false; clearInterval(lTimer); qr.textContent = ''; st.textContent = '看一下现在登没登…'; op.hidden = true;
      const get = async (q) => (await fetch(B + 'api/login?' + q, { cache: 'no-store', credentials: 'include' })).json();
      try {
        const me = (await get('op=me')).me;
        if (me) st.textContent = '已经登录：' + me.name + (me.vip ? '（会员）' : '（不是会员）') + '。要换账号就再扫一次。';
        const d = await get('op=start');
        if (!d.ok) { st.textContent = d.error || '网易云没给二维码'; return; }
        op.href = d.url; op.hidden = false;
        try { await qrLib(); const q = window.qrcode(0, 'M'); q.addData(d.url); q.make(); qr.innerHTML = q.createSvgTag({ cellSize: 6, margin: 0, scalable: true }); }
        catch (e) { qr.textContent = '二维码画不出来，用下面的按钮'; }
        if (!me) st.textContent = '用网易云 App 扫一扫';
        lTimer = setInterval(async () => {
          if (box.hidden) return clearInterval(lTimer);
          try {
            const c = await get('op=check&key=' + encodeURIComponent(d.key));
            if (!c.ok) return;
            if (c.code === 803) {
              clearInterval(lTimer);
              const m2 = (await get('op=me')).me;
              st.textContent = '登录好了：' + (m2 ? m2.name + (m2.vip ? '（会员）' : '（不是会员）') : '');
              qr.textContent = '✓'; S.plStack = []; if (!$('lib').hidden) renderLib();
            } else if (c.code === 800) { clearInterval(lTimer); st.textContent = '二维码过期了，关掉再开一次'; }
            else if (c.code === 802) st.textContent = '扫到了，在网易云里点"确认登录"';
          } catch (e) {}
        }, 2000);
      } catch (e) { st.textContent = '网易云没回话，等会儿再试'; }
    }

    /* 打开、缩小、关掉 */
    function open() { sheet.classList.remove('off'); paintMini(); paintSong(); paintPlay(); tick(); if (!S.queue.length) showLib(true); }
    function minimize() { sheet.classList.add('off'); if (!audio.paused) S.closed = false; paintMini(); }
    function close() {
      togetherEnd();
      S.resume = audio.currentTime || 0; S.closed = true; save();
      audio.pause(); audio.removeAttribute('src'); try { audio.load(); } catch (e) {}
      mini.classList.remove('closing'); paintMini(); paintPlay();
      if ('mediaSession' in navigator) try { navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none'; } catch (e) {}
    }

    /* 事件 */
    root.addEventListener('click', (e) => {
      const a = e.target.closest('[data-a]'); if (!a || !root.contains(a)) return;
      const k = a.dataset.a;
      if (k === 'min') minimize();
      else if (k === 'lib') showLib($('lib').hidden);
      else if (k === 'play') toggle();
      else if (k === 'prev') { if (audio.currentTime > 4) audio.currentTime = 0; else playAt(Math.max(0, S.idx - 1)); }
      else if (k === 'next') playAt(S.idx + 1);
      else if (k === 'login') login();
      else if (k === 'lclose') { $('login').hidden = true; clearInterval(lTimer); }
    });
    root.querySelectorAll('.mev-tabs button').forEach(b => b.onclick = () => {
      S.tab = b.dataset.t; root.querySelectorAll('.mev-tabs button').forEach(x => x.classList.toggle('on', x === b));
      $('searchBox').hidden = S.tab !== 'q'; if (S.tab === 'q') $('search').focus();
      renderLib();
    });
    $('search').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); renderLib(); } });
    $('main').addEventListener('click', () => {   // 点唱片 ↔ 全屏歌词
      const m = $('main'); m.classList.toggle('lyr');
      if (m.classList.contains('lyr')) { S.lyrHold = 0; requestAnimationFrame(() => lyricFollow(false)); }
    });
    (() => {   // 歌词能手滑：手指碰着、或松手后 5 秒内不自动跟
      const box = $('lyric'), hold = () => { S.lyrHold = Date.now() + 5000; };
      box.addEventListener('touchstart', () => { S.lyrTouch = true; }, { passive: true });
      box.addEventListener('touchend', () => { S.lyrTouch = false; hold(); }, { passive: true });
      box.addEventListener('touchcancel', () => { S.lyrTouch = false; hold(); }, { passive: true });
      box.addEventListener('wheel', hold, { passive: true });
    })();
    const seek = $('seek');
    seek.addEventListener('input', () => { S.seeking = true; seek.style.setProperty('--p', (seek.value / 10) + '%'); });
    seek.addEventListener('change', () => { S.seeking = false; const d = audio.duration; if (d) audio.currentTime = seek.value / 1000 * d; });
    audio.addEventListener('play', () => { paintPlay(); report(true); });
    audio.addEventListener('pause', () => { paintPlay(); save(); report(true); });
    audio.addEventListener('seeked', () => report(true));
    audio.addEventListener('timeupdate', tick);
    audio.addEventListener('ended', () => { const t = S.queue[S.idx]; if (S.together && t && t.id === S.together.id) togetherEnd(true); playAt(S.idx + 1); });
    audio.addEventListener('error', () => { if (S.idx >= 0 && audio.src) { const at = audio.currentTime; audio.removeAttribute('src'); playAt(S.idx, at); } });   // 地址过期（二十来分钟）：重新要一个，接着放
    if ('mediaSession' in navigator) {
      const ms = navigator.mediaSession, h = (a, f) => { try { ms.setActionHandler(a, f); } catch (e) {} };
      h('play', () => audio.play()); h('pause', () => audio.pause());
      h('previoustrack', () => playAt(Math.max(0, S.idx - 1))); h('nexttrack', () => playAt(S.idx + 1));
      h('seekto', (e) => { if (e.seekTime != null) audio.currentTime = e.seekTime; });
    }

    /* 小唱片：能拖，松手贴边；点一下放大；长按冒出 关掉 / 一起听 */
    (() => {
      let sx = 0, sy = 0, moved = false, r0 = null, lp = 0, longed = false;
      try { const p = JSON.parse(localStorage.getItem(opts.storageKey + '-mini') || 'null'); if (p) Object.assign(mini.style, { top: p.top + 'px', bottom: 'auto', [p.side === 'l' ? 'left' : 'right']: '14px' }); } catch (e) {}
      mini.addEventListener('touchstart', e => { if (!e.target.closest('.x, .l')) e.preventDefault(); }, { passive: false });   // iOS 按住会去选字
      mini.addEventListener('contextmenu', e => e.preventDefault());
      mini.addEventListener('pointerdown', e => {
        if (e.target.closest('.x, .l')) return;
        sx = e.clientX; sy = e.clientY; moved = false; longed = false; r0 = mini.getBoundingClientRect(); mini.setPointerCapture(e.pointerId);
        clearTimeout(lp); lp = setTimeout(() => { if (!moved) { longed = true; mini.classList.add('closing'); try { navigator.vibrate && navigator.vibrate(10); } catch (e2) {} } }, 500);
      });
      mini.addEventListener('pointermove', e => {
        if (!r0) return; const dx = e.clientX - sx, dy = e.clientY - sy;
        if (!moved && Math.hypot(dx, dy) < 6) return; moved = true; clearTimeout(lp);
        Object.assign(mini.style, { left: (r0.left + dx) + 'px', top: (r0.top + dy) + 'px', right: 'auto', bottom: 'auto' });
      });
      mini.addEventListener('pointerup', () => {
        clearTimeout(lp); if (!r0) return; const r = mini.getBoundingClientRect(); r0 = null;
        if (longed) return;
        if (mini.classList.contains('closing')) return mini.classList.remove('closing');
        if (!moved) return open();
        const left = r.left + r.width / 2 < innerWidth / 2, top = Math.max(60, Math.min(innerHeight - 140, r.top));
        Object.assign(mini.style, { left: left ? '14px' : 'auto', right: left ? 'auto' : '14px', top: top + 'px' });
        try { localStorage.setItem(opts.storageKey + '-mini', JSON.stringify({ side: left ? 'l' : 'r', top: Math.round(top) })); } catch (e) {}
      });
      mini.querySelector('.x').addEventListener('click', (e) => { e.stopPropagation(); close(); });
      mini.querySelector('.l').addEventListener('click', (e) => { e.stopPropagation(); mini.classList.remove('closing'); listen(S.queue[S.idx]); });
      document.addEventListener('pointerdown', (e) => { if (mini.classList.contains('closing') && !mini.contains(e.target)) mini.classList.remove('closing'); }, true);
    })();

    /* 外观（docs/skin.md）：skin.css 叠在 vinyl.css 后面；AI 用 set_skin / use_skin 换了，/api/cmd 里的 skin 号会变，重新拿 */
    let skinLink = null, skinSeen = 0;
    function loadSkin(v) {
      if (opts.skin === false) return;
      if (!skinLink) { skinLink = document.createElement('link'); skinLink.rel = 'stylesheet'; document.head.appendChild(skinLink); }
      skinLink.href = B + 'skin.css?v=' + v;
    }
    loadSkin(Date.now());

    /* AI 点的歌（MCP 的 play）：每 4 秒问一次。处理过的序号记在浏览器里，刷新页面不会重放同一首；
       后端重启后序号从头数（靠 boot 认出来） */
    const CK = opts.storageKey + '-cmd';
    let seen = { boot: '', seq: 0 };
    try { seen = Object.assign(seen, JSON.parse(localStorage.getItem(CK) || '{}')); } catch (e) {}
    async function pollCmd() {
      try {
        const since = seen.boot ? seen.seq : 0;
        const d = await (await fetch(B + 'api/cmd?since=' + since, { cache: 'no-store', credentials: 'include' })).json();
        if (!d.ok) return;
        if (d.boot !== seen.boot) { seen = { boot: d.boot, seq: 0 }; }
        if (skinSeen && d.skin !== skinSeen) loadSkin(d.skin);
        skinSeen = d.skin;
        const c = d.cmd;
        if (c && c.seq > seen.seq) {
          const t = c.track, idle = S.idx < 0 || !S.queue[S.idx];
          if (c.mode === 'now' || idle) {
            const k = S.queue.findIndex(x => x.id === t.id);
            if (k >= 0) playAt(k); else { S.queue.splice(S.idx + 1, 0, t); playAt(S.idx + 1); }
            if (!$('lib').hidden) showLib(false);   // 停在歌单页的话切回播放页
          } else queueOp(t, 'next');   // 排下一首：不打断她正在听的（暂停着也不替她按播放）
          toast((c.note ? c.note + ' · ' : '') + '给你点了《' + t.name + '》' + (c.mode === 'now' || idle ? '' : '，排在下一首'), 6000);
        }
        seen.seq = Math.max(seen.seq, d.seq || 0);
        try { localStorage.setItem(CK, JSON.stringify(seen)); } catch (e) {}
      } catch (e) {}
    }
    pollCmd(); setInterval(pollCmd, 4000);
    mkPoll(800);

    load(); paintSong(); paintPlay();
    if (opts.open) open();
    return {
      open, minimize, close, listen, makeCard,
      play(track) { if (!track || !track.id) return; const k = S.queue.findIndex(x => x.id === track.id); if (k >= 0) playAt(k); else { S.queue.splice(S.idx + 1, 0, track); playAt(S.idx + 1); } },
      state() { const t = S.queue[S.idx]; return { track: t || null, pos: audio.currentTime || 0, dur: audio.duration || 0, playing: !audio.paused, line: (S.lrc[S.curLine] || {}).text || '' }; },
      audio,
    };
  }

  window.MusicEarsVinyl = { mount, parseLrc };
})();
