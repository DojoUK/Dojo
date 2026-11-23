/* The club dashboard: charts for every viewer, and moving, resizing, adding and
   removing widgets for admins. Layouts are measured in 50px cells. */
(function () {
    'use strict';

    const CELL = 50;
    const grid = document.getElementById('dashGrid');
    if (!grid) return;

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

    let items = [];
    let editing = false;
    let dirty = false;

    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));
    const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    const columns = () => Math.max(1, Math.floor(grid.clientWidth / CELL));
    const widest = () => Math.max(columns(), ...items.map((i) => i.x + i.w));

    function itemFrom(el) {
        const d = el.dataset;
        return {
            el, key: d.key, title: d.title,
            x: +d.x, y: +d.y, w: +d.w, h: +d.h,
            minW: +d.minW, minH: +d.minH, maxW: +d.maxW, maxH: +d.maxH,
        };
    }

    function readItems() {
        items = Array.from(grid.querySelectorAll('.dash-item')).map(itemFrom);
    }

    function place(item) {
        item.el.style.gridArea = `${item.y + 1} / ${item.x + 1} / span ${item.h} / span ${item.w}`;
        item.el.style.setProperty('--h', item.h);
        Object.assign(item.el.dataset, { x: item.x, y: item.y, w: item.w, h: item.h });
        const size = item.el.querySelector('.dash-size');
        if (size) size.textContent = `${item.w} × ${item.h}`;
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
        grid.style.minHeight = editing ? `${(bottom + 6) * CELL}px` : '';
        emptyBoard.classList.toggle('d-none', items.length > 0 || editing);
    }

    function redraw() {
        items.forEach(place);
        grow();
    }

    function firstFit(w, h) {
        const cols = Math.max(columns(), w);
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
        grow();
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
        const maxColumns = widest();
        let changed = false;
        el.classList.add('dash-active');

        const onMove = (ev) => {
            const dx = Math.round((ev.pageX - start.px) / CELL);
            const dy = Math.round((ev.pageY - start.py) / CELL);
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
        const maxColumns = widest();
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
            setEditing(false);
            redraw();
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

    readItems();
})();
