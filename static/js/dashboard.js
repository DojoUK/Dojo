/* The club dashboard: fitting the grid to the space it has and charts for every
   viewer, and moving, resizing, adding and removing widgets for admins.

   Layouts are measured on a grid of 24 columns (organisations/dashboard.py) that
   stretches across the space it has; rows grow and shrink with the columns. When
   the space is too narrow for a widget (its data-min-px), the widgets in that row
   are widened, shared evenly across the width, and flow onto more rows. That
   happens only on screen: Customise always shows and edits the saved layout. */
(function () {
    'use strict';

    const grid = document.getElementById('dashGrid');
    if (!grid) return;
    const COLUMNS = Number(grid.dataset.columns) || 24;
    const ROW_MIN = 40;          // px: rows never get shorter than this...
    const ROW_MAX = 64;          // ...or taller than this, however wide the screen
    const EDIT_MIN_CELL = 20;    // px: narrower columns (phones) are too fiddly to drag about

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

    // ── Fitting the grid to the space it has ────────────────────────────
    let items = [];
    let editing = false;
    let afterArrange = () => {};     // the customising code below adds to this

    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));
    const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    const cellWidth = () => grid.clientWidth / COLUMNS;
    const rowHeight = () => clamp(Math.round(cellWidth()), ROW_MIN, ROW_MAX);

    function itemFrom(el) {
        const d = el.dataset;
        return {
            el, key: d.key, title: d.title,
            x: +d.x, y: +d.y, w: +d.w, h: +d.h,
            minW: +d.minW, minH: +d.minH, maxW: +d.maxW, maxH: +d.maxH, minPx: +d.minPx || 0,
        };
    }

    function readItems() {
        items = Array.from(grid.querySelectorAll('.dash-item')).map(itemFrom);
    }

    function show(item, spot) {
        item.el.style.gridArea = `${spot.y + 1} / ${spot.x + 1} / span ${spot.h} / span ${spot.w}`;
        item.el.style.setProperty('--h', spot.h);
    }

    // The highest free spot at or below row `fromY`, trying column `preferX` first.
    function findSpot(placed, w, h, fromY, preferX) {
        for (let y = fromY; y < 1000; y++) {
            const xs = [preferX];
            for (let x = 0; x + w <= COLUMNS; x++) xs.push(x);
            for (const x of xs) {
                if (!placed.some((p) => overlaps({ x, y, w, h }, p))) return { x, y };
            }
        }
        return { x: 0, y: fromY };
    }

    // Where each widget goes on this screen: the saved layout if every widget is at least its
    // minimum width, otherwise the saved layout reflowed. Widgets that start on the same row
    // stay together; in a row with a squeezed widget, they share the width evenly, as many to
    // a row as fit, and the rows that follow move down to make room.
    function flowed() {
        const cell = cellWidth();
        const sorted = items.slice().sort((a, b) => a.y - b.y || a.x - b.x);
        const fits = (i) => i.w * cell >= i.minPx - 0.5;
        if (sorted.every(fits)) return sorted.map((i) => ({ item: i, x: i.x, y: i.y, w: i.w, h: i.h }));

        const runs = [];
        sorted.forEach((i) => {
            const run = runs[runs.length - 1];
            if (run && run[0].y === i.y) run.push(i);
            else runs.push([i]);
        });
        const placed = [];
        let cursor = 0;
        runs.forEach((run) => {
            const squeezed = !run.every(fits);
            let perRow = 0;
            if (squeezed) {
                const need = run.map((i) => Math.min(COLUMNS, Math.ceil(i.minPx / cell)));
                const most = Math.max(1, Math.min(...need.map((n) => Math.floor(COLUMNS / n))));
                perRow = Math.ceil(run.length / Math.ceil(run.length / most));
            }
            run.forEach((item, n) => {
                let w = item.w;
                if (perRow) {
                    // Share the 24 columns between the widgets in this row, spare columns to the first.
                    const base = Math.floor(COLUMNS / perRow);
                    w = base + ((n % perRow) < COLUMNS - base * perRow ? 1 : 0);
                }
                const spot = findSpot(placed, w, item.h, cursor, perRow ? 0 : Math.min(item.x, COLUMNS - w));
                placed.push({ item, x: spot.x, y: spot.y, w, h: item.h });
                cursor = spot.y;
            });
        });
        return placed;
    }

    function arrange() {
        grid.style.setProperty('--row', `${rowHeight()}px`);
        const spots = editing
            ? items.map((i) => ({ item: i, x: i.x, y: i.y, w: i.w, h: i.h }))
            : flowed();
        spots.forEach((spot) => show(spot.item, spot));
        grid.classList.toggle('dash-flowed', spots.some((s) => s.x !== s.item.x || s.y !== s.item.y || s.w !== s.item.w));
        grid.classList.add('dash-ready');
        afterArrange();
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
    const emptyBoard = document.getElementById('dashEmpty');
    const catalogueBody = document.getElementById('dashCatalogueBody');
    const catalogueEl = document.getElementById('dashCatalogue');
    const saveButton = document.getElementById('dashSave');
    const csrf = document.querySelector('input[name=csrfmiddlewaretoken]').value;

    let dirty = false;

    // Keeps a widget's saved place on the page, for saving and for Customise.
    function record(item) {
        Object.assign(item.el.dataset, { x: item.x, y: item.y, w: item.w, h: item.h });
        const size = item.el.querySelector('.dash-size');
        if (size) size.textContent = `${item.w} × ${item.h}`;
    }

    // Customising needs room to drag: below that the button explains why it's off.
    function updateCustomise() {
        const roomy = cellWidth() >= EDIT_MIN_CELL;
        customise.disabled = !roomy && !editing;
        customise.title = roomy ? '' : 'Make the window wider to customise the dashboard';
    }

    // Keeps `moved` where it is and pushes anything it lands on further down.
    function resolve(moved) {
        const placed = moved ? [moved] : [];
        items.filter((i) => i !== moved)
            .sort((a, b) => a.y - b.y || a.x - b.x)
            .forEach((item) => {
                let hit;
                while ((hit = placed.find((p) => overlaps(p, item)))) item.y = hit.y + hit.h;
                placed.push(item);
            });
    }

    function grow() {
        const bottom = Math.max(0, ...items.map((i) => i.y + i.h));
        grid.style.minHeight = editing ? `${(bottom + 6) * rowHeight()}px` : '';
        emptyBoard.classList.toggle('d-none', items.length > 0 || editing);
    }

    afterArrange = () => {
        grow();
        updateCustomise();
    };

    function redraw() {
        items.forEach(record);
        arrange();
    }

    function firstFit(w, h) {
        const cols = COLUMNS;
        for (let y = 0; y < 400; y++) {
            for (let x = 0; x + w <= cols; x++) {
                const spot = { x, y, w, h };
                if (!items.some((i) => overlaps(spot, i))) return { x, y };
            }
        }
        return { x: 0, y: Math.max(0, ...items.map((i) => i.y + i.h)) };
    }

    function markDirty() {
        dirty = true;
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
            saveButton.disabled = true;
            buildCatalogue();
        }
        arrange();      // Customise shows the saved layout; the view fits it to the screen
    }

    // ── Dragging and resizing ──────────────────────────────────────────
    grid.addEventListener('pointerdown', (e) => {
        if (!editing || e.button !== 0) return;
        const el = e.target.closest('.dash-item');
        if (!el || e.target.closest('.dash-remove')) return;
        const item = items.find((i) => i.el === el);
        if (!item) return;
        e.preventDefault();
        el.focus({ preventScroll: true });
        el.setPointerCapture(e.pointerId);

        const resizing = Boolean(e.target.closest('.dash-resize'));
        const start = { px: e.pageX, py: e.pageY, x: item.x, y: item.y, w: item.w, h: item.h };
        const snapshot = items.map((i) => ({ i, x: i.x, y: i.y }));
        const maxColumns = COLUMNS;
        const cell = cellWidth();
        const row = rowHeight();
        let changed = false;
        el.classList.add('dash-active');

        const onMove = (ev) => {
            const dx = Math.round((ev.pageX - start.px) / cell);
            const dy = Math.round((ev.pageY - start.py) / row);
            snapshot.forEach((s) => { s.i.x = s.x; s.i.y = s.y; });
            if (resizing) {
                item.w = clamp(start.w + dx, item.minW, Math.min(item.maxW, maxColumns - item.x));
                item.h = clamp(start.h + dy, item.minH, item.maxH);
            } else {
                item.x = clamp(start.x + dx, 0, maxColumns - item.w);
                item.y = Math.max(0, start.y + dy);
            }
            resolve(item);
            redraw();
            changed = changed || dx !== 0 || dy !== 0;
        };
        const onUp = () => {
            el.removeEventListener('pointermove', onMove);
            el.removeEventListener('pointerup', onUp);
            el.removeEventListener('pointercancel', onUp);
            el.classList.remove('dash-active');
            if (changed) markDirty();
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
        if (!step) return;
        e.preventDefault();
        const maxColumns = COLUMNS;
        if (e.shiftKey) {
            item.w = clamp(item.w + step[0], item.minW, Math.min(item.maxW, maxColumns - item.x));
            item.h = clamp(item.h + step[1], item.minH, item.maxH);
        } else {
            item.x = clamp(item.x + step[0], 0, maxColumns - item.w);
            item.y = Math.max(0, item.y + step[1]);
        }
        resolve(item);
        redraw();
        markDirty();
    });

    // ── Removing and adding ────────────────────────────────────────────
    function remove(item) {
        const next = item.el.nextElementSibling || item.el.previousElementSibling;
        item.el.remove();
        items = items.filter((i) => i !== item);
        grow();
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
        const spot = firstFit(entry.w, entry.h);
        const url = config.widgetUrl.replace('__key__', entry.key)
            + `?x=${spot.x}&y=${spot.y}&w=${entry.w}&h=${entry.h}`;
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
            resolve(item);
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

    saveButton.addEventListener('click', async () => {
        saveButton.disabled = true;
        const label = saveButton.innerHTML;
        saveButton.textContent = 'Saving…';
        try {
            const result = await post({ widgets: items.map(({ key, x, y, w, h }) => ({ key, x, y, w, h })) });
            const byKey = Object.fromEntries(result.widgets.map((w) => [w.key, w]));
            items = items.filter((i) => {
                const saved = byKey[i.key];
                if (!saved) { i.el.remove(); return false; }
                Object.assign(i, saved);
                return true;
            });
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

    // Moves every widget up as far as it will go, closing the gaps left by removed or moved widgets.
    document.getElementById('dashTidy').addEventListener('click', () => {
        const placed = [];
        items.slice().sort((a, b) => a.y - b.y || a.x - b.x).forEach((item) => {
            while (item.y > 0 && !placed.some((p) => overlaps({ ...item, y: item.y - 1 }, p))) item.y -= 1;
            placed.push(item);
        });
        redraw();
        markDirty();
    });

    customise.addEventListener('click', () => {
        if (cellWidth() < EDIT_MIN_CELL) return;
        readItems();
        setEditing(true);
    });

    document.getElementById('dashCancel').addEventListener('click', () => {
        if (dirty) {
            dirty = false;
            window.location.reload();     // puts back the saved layout, and any removed widgets' content
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

    updateCustomise();
})();
