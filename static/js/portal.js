/*
 * Member portal interactions, after Apple's "Designing Fluid Interfaces":
 *  - press feedback on pointer-down (iOS Safari only applies :active with a touch listener)
 *  - the top bar's hairline and compact title appear once content scrolls under it
 *  - the "More" sheet: springs that start from the on-screen position, 1:1 dragging,
 *    momentum projection on release, rubber-banding past the top, and it can be
 *    grabbed and reversed at any moment.
 */
(function () {
    'use strict';

    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    // Instant :active on touch devices.
    document.addEventListener('touchstart', function () {}, { passive: true });

    // ── Dark mode: the member's own choice, kept in this browser ──────────────
    // Every [data-p-theme-toggle] (the moon button on desktop, the switch in the More
    // sheet) flips it, and all of them show the same state.
    var root = document.documentElement;
    var toggles = document.querySelectorAll('[data-p-theme-toggle]');
    function isDark() { return root.getAttribute('data-bs-theme') === 'dark'; }
    function showState() {
        var dark = isDark();
        toggles.forEach(function (t) {
            if (t.type === 'checkbox') t.checked = dark;
            else t.setAttribute('aria-pressed', dark ? 'true' : 'false');
        });
    }
    function setDark(dark) {
        root.setAttribute('data-bs-theme', dark ? 'dark' : 'light');
        try { localStorage.setItem('portalTheme', dark ? 'dark' : 'light'); } catch (e) {}
        showState();
    }
    toggles.forEach(function (t) {
        t.addEventListener(t.type === 'checkbox' ? 'change' : 'click', function () {
            setDark(t.type === 'checkbox' ? t.checked : !isDark());
        });
    });
    showState();

    // ── Scroll edge: hairline + compact title only when something is underneath ──
    var bar = document.querySelector('.p-bar');
    var sentinel = document.querySelector('.p-title');
    if (bar && sentinel && 'IntersectionObserver' in window) {
        new IntersectionObserver(function (entries) {
            bar.classList.toggle('is-scrolled', !entries[0].isIntersecting);
        }, { rootMargin: '-44px 0px 0px 0px' }).observe(sentinel);
    }

    // ── Spring ──────────────────────────────────────────────────────────────
    // Apple's designer-friendly parameters: damping ratio (1 = no overshoot) and
    // response (seconds; lower is snappier). Integrated per frame, so a new target
    // just re-aims the motion and the current velocity carries through.
    function Spring(value, onUpdate) {
        this.value = value;
        this.velocity = 0;
        this.target = value;
        this.onUpdate = onUpdate;
        this.frame = null;
    }
    Spring.prototype.to = function (target, opts) {
        opts = opts || {};
        this.target = target;
        if (opts.velocity !== undefined) this.velocity = opts.velocity;
        var response = opts.response || 0.35;
        var damping = opts.damping === undefined ? 1 : opts.damping;
        this.stiffness = Math.pow(2 * Math.PI / response, 2);
        this.friction = 4 * Math.PI * damping / response;
        this.onRest = opts.onRest || null;
        if (!this.frame) {
            this.last = performance.now();
            this.frame = requestAnimationFrame(this.step.bind(this));
        }
    };
    Spring.prototype.step = function (now) {
        var dt = Math.min((now - this.last) / 1000, 1 / 30);
        this.last = now;
        // Sub-steps keep stiff springs stable at low frame rates.
        for (var i = 0; i < 4; i++) {
            var h = dt / 4;
            var force = -this.stiffness * (this.value - this.target) - this.friction * this.velocity;
            this.velocity += force * h;
            this.value += this.velocity * h;
        }
        if (Math.abs(this.velocity) < 2 && Math.abs(this.value - this.target) < 0.5) {
            this.value = this.target;
            this.velocity = 0;
            this.frame = null;
            this.onUpdate(this.value);
            if (this.onRest) this.onRest();
            return;
        }
        this.onUpdate(this.value);
        this.frame = requestAnimationFrame(this.step.bind(this));
    };
    Spring.prototype.stop = function () {
        if (this.frame) cancelAnimationFrame(this.frame);
        this.frame = null;
    };

    // Where a flick would come to rest (Apple's projection, scroll-like deceleration).
    function project(velocity, rate) {
        rate = rate || 0.998;
        return (velocity / 1000) * rate / (1 - rate);
    }
    // Soft boundary: the further past it, the less the sheet follows.
    function rubberband(overshoot, dimension) {
        var c = 0.55;
        return (overshoot * dimension * c) / (dimension + c * Math.abs(overshoot));
    }

    // ── "More" sheet ────────────────────────────────────────────────────────
    var sheet = document.getElementById('p-sheet');
    var scrim = document.getElementById('p-scrim');
    var opener = document.getElementById('p-more');
    if (!sheet || !scrim || !opener) return;

    var main = document.getElementById('p-main');
    var open = false;
    var height = 0;
    var position = new Spring(0, render);

    function measure() { height = sheet.getBoundingClientRect().height || window.innerHeight * 0.88; }

    // y: 0 = fully open, height = fully closed.
    function render(y) {
        sheet.style.transform = 'translate3d(0,' + y + 'px,0)';
        var progress = height ? Math.max(0, Math.min(1, 1 - y / height)) : 0;
        scrim.style.opacity = progress;
    }

    function setOpen(state) {
        open = state;
        opener.setAttribute('aria-expanded', state ? 'true' : 'false');
        scrim.style.pointerEvents = state ? 'auto' : 'none';
        if (main) { if (state) main.setAttribute('inert', ''); else main.removeAttribute('inert'); }
        document.documentElement.style.overflow = state ? 'hidden' : '';
    }

    function show(velocity) {
        sheet.classList.add('is-open');
        measure();
        if (!position.frame && !open) position.value = height;   // start from below, unless mid-flight
        setOpen(true);
        if (reduceMotion.matches) {
            position.stop(); position.value = 0;
            sheet.style.transition = 'opacity .2s ease'; sheet.style.opacity = 1; render(0);
        } else {
            // No momentum behind a tap, so no bounce; a drag that flicked it open gets a little.
            position.to(0, { response: 0.35, damping: velocity ? 0.85 : 1, velocity: velocity || position.velocity });
        }
        var first = sheet.querySelector('.p-sheet-close');
        if (first) first.focus({ preventScroll: true });
    }

    function hide(velocity) {
        setOpen(false);
        function done() { if (!open) { sheet.classList.remove('is-open'); opener.focus({ preventScroll: true }); } }
        if (reduceMotion.matches) {
            sheet.style.transition = 'opacity .2s ease'; sheet.style.opacity = 0;
            setTimeout(function () { position.value = height; render(height); sheet.style.opacity = ''; done(); }, 200);
        } else {
            // Leaves the way it came: back down.
            position.to(height, { response: 0.3, damping: 1, velocity: velocity || position.velocity, onRest: done });
        }
    }

    opener.addEventListener('click', function () { open ? hide() : show(); });
    scrim.addEventListener('click', function () { hide(); });
    sheet.querySelector('.p-sheet-close').addEventListener('click', function () { hide(); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && open) hide();
        // Keep keyboard focus inside the open sheet.
        if (e.key === 'Tab' && open) {
            var focusable = sheet.querySelectorAll('a[href], button:not([disabled])');
            if (!focusable.length) return;
            var first = focusable[0], last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    });
    window.addEventListener('resize', function () { if (open) { measure(); } });

    // Dragging, from the grabber or the header (the list keeps its own native scrolling).
    // The sheet follows the finger 1:1 from wherever it was grabbed, even mid-animation.
    var drag = null;
    sheet.addEventListener('pointerdown', function (e) {
        if (!open || e.button > 0) return;
        if (!e.target.closest('.p-sheet-grab, .p-sheet-head') || e.target.closest('.p-sheet-close')) return;
        position.stop();                          // grab it mid-flight, from where it is on screen
        drag = { id: e.pointerId, startY: e.clientY, startPos: position.value, active: true,
                 history: [{ y: e.clientY, t: e.timeStamp }] };
        try { sheet.setPointerCapture(e.pointerId); } catch (err) {}
        sheet.style.cursor = 'grabbing';
    });
    sheet.addEventListener('pointermove', function (e) {
        if (!drag || e.pointerId !== drag.id) return;
        drag.history.push({ y: e.clientY, t: e.timeStamp });
        if (drag.history.length > 6) drag.history.shift();
        var y = drag.startPos + (e.clientY - drag.startY);
        if (y < 0) y = rubberband(y, height);    // pulling past fully open resists
        position.value = y;
        render(y);
    });
    function release(e) {
        if (!drag || e.pointerId !== drag.id) return;
        var d = drag; drag = null;
        sheet.style.cursor = '';
        if (!d.active) return;
        // Release velocity from the last ~100ms of movement, in px/s.
        var h = d.history, a = h[0], b = h[h.length - 1];
        for (var i = h.length - 1; i >= 0; i--) { if (b.t - h[i].t > 100) { a = h[i]; break; } a = h[i]; }
        var velocity = b.t > a.t ? (b.y - a.y) / ((b.t - a.t) / 1000) : 0;
        // Decide from where the gesture is going, not where it let go.
        var projected = position.value + project(velocity);
        if (projected > height * 0.5) hide(velocity);
        else show(velocity);
    }
    sheet.addEventListener('pointerup', release);
    sheet.addEventListener('pointercancel', release);
})();
