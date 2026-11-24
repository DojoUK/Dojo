/* The club dashboard: fitting the grid to the screen and drawing charts for everyone, and
   moving, resizing, adding and removing widgets for admins.

   Each kind of screen has its own arrangement of the same widgets (organisations/dashboard.py):
   - wide, for computers and tablets held sideways: 24 columns;
   - tablet, for tablets held upright and narrow windows: 12 columns;
   - phone: one widget per row, in the wide layout's order.
   Which one shows depends on the width the grid has, and changes as a window is resized or a
   tablet turned. Columns stretch to fill the width and rows follow them, so cells stay roughly
   square. Every widget is at least as wide as it reads well at the narrowest screen of each size,
   so a layout always shows exactly as arranged: Customise edits what you see. */
(function () {
    'use strict';

    const grid = document.getElementById('dashGrid');
    if (!grid) return;
    const SIZES = {
        wide: { columns: Number(grid.dataset.columns) || 24, from: Number(grid.dataset.wideFrom) || 880 },
        tablet: { columns: Number(grid.dataset.tabletColumns) || 12, from: Number(grid.dataset.tabletFrom) || 480 },
    };
    const ROW_MIN = 40;          // px: rows never get shorter than this...
    const ROW_MAX = 64;          // ...or taller than this, however wide the screen
    const PHONE_ROW = 44;

    // ── Charts ──────────────────────────────────────────────────────────
    function cssVar(name, fallback) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    }

    function fill(colour) {
        return /^#[0-9a-f]{6}$/i.test(colour) ? colour + '29' : 'rgba(37, 99, 235, .16)';
    }

    function makeChart(canvas) {
        if (canvas.dashChart || typeof Chart === 'undefined') return;
        let spec;
        try { spec = JSON.parse(canvas.dataset.chart); } catch (e) { return; }
        const accent = cssVar('--dojo-accent', '#2563EB');
        const muted = cssVar('--dojo-muted', '#64748b');
        const border = cssVar('--dojo-border', '#e2e8f0');
        const money = (v) => spec.prefix + Number(v).toLocaleString('en-GB', { maximumFractionDigits: 0 });
        const show = (v) => (spec.prefix ? money(v) : v);
        const line = spec.type === 'line';
        canvas.dashChart = new Chart(canvas, {
            type: spec.type,
            data: {
                labels: spec.labels,
                datasets: spec.series.map((s) => ({
                    label: s.label,
                    data: s.data,
                    backgroundColor: line ? fill(accent) : accent,
                    borderColor: accent,
                    borderWidth: line ? 2 : 0,
                    borderRadius: 4,
                    fill: line,
                    tension: 0.3,
                    pointRadius: line ? 2 : 0,
                    maxBarThickness: 32,
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 300 },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + show(c.parsed.y) } },
                },
                scales: {
                    x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkipPadding: 8 } },
                    y: {
                        beginAtZero: true,
                        grid: { color: border },
                        border: { display: false },
                        ticks: { color: muted, precision: 0, maxTicksLimit: 6, callback: show },
                    },
                },
            },
        });
    }

    function initCharts(root) {
        root.querySelectorAll('canvas[data-chart]').forEach(makeChart);
    }

    if (typeof Chart !== 'undefined') initCharts(grid);
    else window.addEventListener('load', () => initCharts(grid));

    // ── Fitting the grid to the screen ──────────────────────────────────
    let items = [];
    let editing = false;
    let afterArrange = () => {};     // the customising code below adds to this

    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));
    const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

    function sizeNow() {
        const width = grid.clientWidth;
        if (width >= SIZES.wide.from) return 'wide';
        return width >= SIZES.tablet.from ? 'tablet' : 'phone';
    }
    const columns = (size) => (size === 'phone' ? 1 : SIZES[size].columns);
    const cellWidth = (size) => grid.clientWidth / columns(size);
    const rowHeight = (size) => (size === 'phone' ? PHONE_ROW : clamp(Math.round(cellWidth(size)), ROW_MIN, ROW_MAX));

    function itemFrom(el) {
        const d = el.dataset;
        const item = {
            el, key: d.key, title: d.title, minH: +d.minH, maxH: +d.maxH,
            limits: {
                wide: { minW: +d.minW, maxW: +d.maxW },
                tablet: { minW: +d.tabletMinW, maxW: +d.tabletMaxW },
            },
            pos: {
                wide: { x: +d.x, y: +d.y, w: +d.w, h: +d.h },
                tablet: { x: +d.tabletX, y: +d.tabletY, w: +d.tabletW, h: +d.tabletH },
            },
        };
        Object.values(item.pos).forEach((p) => { p.item = item; });
        return item;
    }

    function readItems() {
        items = Array.from(grid.querySelectorAll('.dash-item')).map(itemFrom);
    }

    // The widgets' places at one size, each knowing its widget: [{x, y, w, h, item}].
    const placesAt = (size) => items.map((i) => i.pos[size]);

    function show(item, spot) {
        item.el.style.gridArea = `${spot.y + 1} / ${spot.x + 1} / span ${spot.h} / span ${spot.w}`;
        item.el.style.setProperty('--h', spot.h);
    }

    function arrange() {
        const size = sizeNow();
        grid.dataset.size = size;
        grid.style.setProperty('--cols', columns(size));
        grid.style.setProperty('--row', `${rowHeight(size)}px`);
        if (size === 'phone') {
            // One widget per row, in the wide layout's reading order.
            let y = 0;
            items.slice().sort((a, b) => a.pos.wide.y - b.pos.wide.y || a.pos.wide.x - b.pos.wide.x).forEach((item) => {
                show(item, { x: 0, y, w: 1, h: item.pos.wide.h });
                y += item.pos.wide.h;
            });
        } else {
            items.forEach((item) => show(item, item.pos[size]));
        }
        grid.classList.add('dash-ready');
        afterArrange(size);
    }

    // Re-fit whenever the space changes width: a window resized, a tablet turned, the sidebar shown.
    let lastWidth = 0;
    let pending = false;
    new ResizeObserver(() => {
        if (grid.clientWidth === lastWidth || pending) return;
        pending = true;
        requestAnimationFrame(() => {
            pending = false;
            lastWidth = grid.clientWidth;
            arrange();
        });
    }).observe(grid);

    readItems();
    lastWidth = grid.clientWidth;
    arrange();

    // ── Customising (admins only) ───────────────────────────────────────
    const configEl = document.getElementById('dash-config');
    const customise = document.getElementById('dashCustomise');
    if (!configEl || !customise) return;

    const config = JSON.parse(configEl.textContent);
    const viewBar = document.getElementById('dashViewBar');
    const editBar = document.getElementById('dashEditBar');
    const editSize = document.getElementById('dashEditSize');
    const emptyBoard = document.getElementById('dashEmpty');
    const catalogueBody = document.getElementById('dashCatalogueBody');
    const catalogueEl = document.getElementById('dashCatalogue');
    const saveButton = document.getElementById('dashSave');
    const csrf = document.querySelector('input[name=csrfmiddlewaretoken]').value;

    let dirty = false;
    let tabletCustom = Boolean(config.tabletCustom);   // the tablet layout has been arranged on a tablet
    const edited = new Set();                          // sizes arranged since Customise was pressed

    // Keeps a widget's places on the page, for saving and for Cancel.
    function record(item) {
        const d = item.el.dataset;
        const { wide, tablet } = item.pos;
        Object.assign(d, { x: wide.x, y: wide.y, w: wide.w, h: wide.h,
                           tabletX: tablet.x, tabletY: tablet.y, tabletW: tablet.w, tabletH: tablet.h });
    }

    function showSize(size) {
        const label = item => item.el.querySelector('.dash-size');
        items.forEach((item) => {
            const p = item.pos[size === 'phone' ? 'wide' : size];
            const el = label(item);
            if (el) el.textContent = `${p.w} × ${p.h}`;
        });
        if (editSize) {
            editSize.textContent = size === 'tablet' ? 'Arranging for tablets' : 'Arranging for computers';
            editSize.title = size === 'tablet'
                ? 'Tablets held upright and narrow windows have their own layout. Turn the tablet or widen the window to arrange the computer layout.'
                : 'Computers and tablets held sideways share this layout. Tablets held upright have their own.';
        }
    }

    // Phones are too narrow to drag widgets about.
    function updateCustomise(size) {
        const roomy = size !== 'phone';
        customise.disabled = !roomy && !editing;
        customise.title = roomy ? '' : 'Use a tablet or computer to customise the dashboard';
        if (editing && !roomy) setEditing(false);
    }

    function grow(size) {
        const places = placesAt(size === 'phone' ? 'wide' : size);
        const bottom = Math.max(0, ...places.map((p) => p.y + p.h));
        grid.style.minHeight = editing ? `${(bottom + 6) * rowHeight(size)}px` : '';
        emptyBoard.classList.toggle('d-none', items.length > 0 || editing);
    }

    afterArrange = (size) => {
        grow(size);
        updateCustomise(size);
        if (editing) showSize(size);
    };

    function redraw() {
        items.forEach(record);
        arrange();
    }

    // Keeps `moved` where it is and pushes anything it lands on further down.
    function resolve(places, moved) {
        const placed = moved ? [moved] : [];
        places.filter((p) => p !== moved)
            .sort((a, b) => a.y - b.y || a.x - b.x)
            .forEach((p) => {
                let hit;
                while ((hit = placed.find((q) => overlaps(q, p)))) p.y = hit.y + hit.h;
                placed.push(p);
            });
    }

    function firstFit(places, w, h, cols) {
        for (let y = 0; y < 400; y++) {
            for (let x = 0; x + w <= cols; x++) {
                const spot = { x, y, w, h };
                if (!places.some((p) => overlaps(spot, p))) return { x, y };
            }
        }
        return { x: 0, y: Math.max(0, ...places.map((p) => p.y + p.h)) };
    }

    function markDirty(size) {
        dirty = true;
        if (size) edited.add(size);
        saveButton.disabled = false;
    }

    function setEditing(on) {
        editing = on;
        grid.classList.toggle('dash-editing', on);
        viewBar.classList.toggle('d-none', on);
        viewBar.classList.toggle('d-flex', !on);
        editBar.classList.toggle('d-none', !on);
        editBar.classList.toggle('d-flex', on);
        items.forEach((i) => {
            if (on) i.el.setAttribute('tabindex', '0');
            else i.el.removeAttribute('tabindex');
        });
        if (on) {
            dirty = false;
            edited.clear();
            saveButton.disabled = true;
            buildCatalogue();
        }
        arrange();
    }

    // ── Dragging and resizing ──────────────────────────────────────────
    grid.addEventListener('pointerdown', (e) => {
        if (!editing || e.button !== 0) return;
        const size = sizeNow();
        if (size === 'phone') return;
        const el = e.target.closest('.dash-item');
        if (!el || e.target.closest('.dash-remove')) return;
        const item = items.find((i) => i.el === el);
        if (!item) return;
        e.preventDefault();
        el.focus({ preventScroll: true });
        el.setPointerCapture(e.pointerId);

        const p = item.pos[size];
        const limits = item.limits[size];
        const cols = columns(size);
        const resizing = Boolean(e.target.closest('.dash-resize'));
        const start = { px: e.pageX, py: e.pageY, x: p.x, y: p.y, w: p.w, h: p.h };
        const places = placesAt(size);
        const snapshot = places.map((q) => ({ q, x: q.x, y: q.y }));
        const cell = cellWidth(size);
        const row = rowHeight(size);
        let changed = false;
        el.classList.add('dash-active');

        const onMove = (ev) => {
            const dx = Math.round((ev.pageX - start.px) / cell);
            const dy = Math.round((ev.pageY - start.py) / row);
            snapshot.forEach((s) => { s.q.x = s.x; s.q.y = s.y; });
            if (resizing) {
                p.w = clamp(start.w + dx, limits.minW, Math.min(limits.maxW, cols - p.x));
                p.h = clamp(start.h + dy, item.minH, item.maxH);
            } else {
                p.x = clamp(start.x + dx, 0, cols - p.w);
                p.y = Math.max(0, start.y + dy);
            }
            resolve(places, p);
            redraw();
            changed = changed || dx !== 0 || dy !== 0;
        };
        const onUp = () => {
            el.removeEventListener('pointermove', onMove);
            el.removeEventListener('pointerup', onUp);
            el.removeEventListener('pointercancel', onUp);
            el.classList.remove('dash-active');
            if (changed) markDirty(size);
        };
        el.addEventListener('pointermove', onMove);
        el.addEventListener('pointerup', onUp);
        el.addEventListener('pointercancel', onUp);
    });

    // Arrow keys move the focused widget; with Shift they resize it. Delete removes it.
    grid.addEventListener('keydown', (e) => {
        if (!editing) return;
        const el = e.target.closest('.dash-item');
        if (!el || e.target !== el) return;
        const item = items.find((i) => i.el === el);
        if (e.key === 'Delete' || e.key === 'Backspace') {
            e.preventDefault();
            remove(item);
            return;
        }
        const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
        const size = sizeNow();
        if (!step || size === 'phone') return;
        e.preventDefault();
        const p = item.pos[size];
        const limits = item.limits[size];
        const cols = columns(size);
        if (e.shiftKey) {
            p.w = clamp(p.w + step[0], limits.minW, Math.min(limits.maxW, cols - p.x));
            p.h = clamp(p.h + step[1], item.minH, item.maxH);
        } else {
            p.x = clamp(p.x + step[0], 0, cols - p.w);
            p.y = Math.max(0, p.y + step[1]);
        }
        resolve(placesAt(size), p);
        redraw();
        markDirty(size);
    });

    // ── Removing and adding ────────────────────────────────────────────
    // A widget is on both layouts: adding or removing it changes both.
    function remove(item) {
        const next = item.el.nextElementSibling || item.el.previousElementSibling;
        item.el.remove();
        items = items.filter((i) => i !== item);
        arrange();
        buildCatalogue();
        markDirty();
        if (next && next.classList.contains('dash-item')) next.focus({ preventScroll: true });
    }

    grid.addEventListener('click', (e) => {
        const button = e.target.closest('.dash-remove');
        if (!button || !editing) return;
        const item = items.find((i) => i.el === button.closest('.dash-item'));
        if (item) remove(item);
    });

    async function add(entry, button) {
        button.disabled = true;
        button.textContent = 'Adding…';
        const wide = firstFit(placesAt('wide'), entry.w, entry.h, SIZES.wide.columns);
        const tablet = firstFit(placesAt('tablet'), entry.tabletW, entry.h, SIZES.tablet.columns);
        const url = config.widgetUrl.replace('__key__', entry.key)
            + `?x=${wide.x}&y=${wide.y}&w=${entry.w}&h=${entry.h}`
            + `&tx=${tablet.x}&ty=${tablet.y}&tw=${entry.tabletW}&th=${entry.h}`;
        try {
            const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok) throw new Error(response.status);
            const holder = document.createElement('div');
            holder.innerHTML = (await response.text()).trim();
            const el = holder.firstElementChild;
            el.classList.add('dash-new');
            el.setAttribute('tabindex', '0');
            grid.appendChild(el);
            const item = itemFrom(el);
            items.push(item);
            redraw();
            initCharts(el);
            markDirty();
            buildCatalogue();
            bootstrap.Offcanvas.getOrCreateInstance(catalogueEl).hide();
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(() => el.classList.remove('dash-new'), 2600);
        } catch (err) {
            button.disabled = false;
            button.textContent = "Couldn't add, try again";
        }
    }

    function buildCatalogue() {
        const onBoard = new Set(items.map((i) => i.key));
        catalogueBody.replaceChildren();
        config.categories.forEach(([category, label]) => {
            const entries = config.catalogue.filter((c) => c.category === category);
            if (!entries.length) return;
            const heading = document.createElement('div');
            heading.className = 'dash-cat-group';
            heading.textContent = label;
            catalogueBody.appendChild(heading);
            entries.forEach((entry) => {
                const row = document.createElement('div');
                row.className = 'dash-cat-row';
                const icon = document.createElement('i');
                icon.className = 'bi ' + entry.icon;
                icon.setAttribute('aria-hidden', 'true');
                const text = document.createElement('div');
                text.className = 'flex-grow-1 small';
                const title = document.createElement('div');
                title.className = 'fw-semibold';
                title.textContent = entry.title;
                const description = document.createElement('div');
                description.className = 'text-muted';
                description.textContent = entry.description;
                text.append(title, description);
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-sm ' + (onBoard.has(entry.key) ? 'btn-light' : 'btn-outline-dark');
                if (onBoard.has(entry.key)) {
                    button.disabled = true;
                    button.textContent = 'Added';
                } else {
                    button.textContent = 'Add';
                    button.setAttribute('aria-label', 'Add ' + entry.title);
                    button.addEventListener('click', () => add(entry, button));
                }
                row.append(icon, text, button);
                catalogueBody.appendChild(row);
            });
        });
    }

    // ── Saving ─────────────────────────────────────────────────────────
    async function post(body) {
        const response = await fetch(config.saveUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(response.status);
        return response.json();
    }

    const placesToSave = (size) => items.map((i) => {
        const { x, y, w, h } = i.pos[size];
        return { key: i.key, x, y, w, h };
    });

    saveButton.addEventListener('click', async () => {
        saveButton.disabled = true;
        const label = saveButton.innerHTML;
        saveButton.textContent = 'Saving…';
        // The tablet layout is kept once it has been arranged on a tablet; until then it follows
        // the wide one, and the server works it out.
        const keepTablet = tabletCustom || edited.has('tablet');
        try {
            const result = await post({
                widgets: placesToSave('wide'),
                tablet: keepTablet ? placesToSave('tablet') : null,
            });
            const wide = Object.fromEntries(result.widgets.map((w) => [w.key, w]));
            const tablet = Object.fromEntries(result.tablet.map((w) => [w.key, w]));
            items = items.filter((i) => {
                if (!wide[i.key]) { i.el.remove(); return false; }
                ['x', 'y', 'w', 'h'].forEach((k) => {
                    i.pos.wide[k] = wide[i.key][k];
                    if (tablet[i.key]) i.pos.tablet[k] = tablet[i.key][k];
                });
                return true;
            });
            tabletCustom = Boolean(result.tabletCustom);
            dirty = false;
            items.forEach(record);
            setEditing(false);
        } catch (err) {
            saveButton.disabled = false;
            alertBar("The layout couldn't be saved. Check your connection and try again.");
        } finally {
            saveButton.innerHTML = label;
        }
    });

    function alertBar(text) {
        const holder = document.querySelector('.dojo-messages') || document.body;
        const alert = document.createElement('div');
        alert.className = 'alert alert-danger alert-dismissible fade show';
        alert.setAttribute('role', 'alert');
        alert.textContent = text;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.setAttribute('data-bs-dismiss', 'alert');
        close.setAttribute('aria-label', 'Close');
        alert.appendChild(close);
        holder.appendChild(alert);
    }

    document.getElementById('dashReset').addEventListener('click', async () => {
        try {
            await post({ reset: true });
            dirty = false;
            window.location.reload();
        } catch (err) {
            alertBar("The dashboard couldn't be reset. Try again.");
        }
    });

    // Moves every widget up as far as it will go in the layout being arranged, closing the gaps.
    document.getElementById('dashTidy').addEventListener('click', () => {
        const size = sizeNow();
        if (size === 'phone') return;
        const placed = [];
        placesAt(size).sort((a, b) => a.y - b.y || a.x - b.x).forEach((p) => {
            while (p.y > 0 && !placed.some((q) => overlaps({ x: p.x, y: p.y - 1, w: p.w, h: p.h }, q))) p.y -= 1;
            placed.push(p);
        });
        redraw();
        markDirty(size);
    });

    customise.addEventListener('click', () => {
        if (sizeNow() === 'phone') return;
        readItems();
        setEditing(true);
    });

    document.getElementById('dashCancel').addEventListener('click', () => {
        if (dirty) {
            dirty = false;
            window.location.reload();     // puts back the saved layouts, and any removed widgets' content
        } else {
            setEditing(false);
        }
    });

    window.addEventListener('beforeunload', (e) => {
        if (editing && dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    updateCustomise(sizeNow());
})();
