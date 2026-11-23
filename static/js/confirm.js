/* Dojo's confirm dialog: a centred alert in place of the browser's confirm() box.

   Mark a button, link or form with data-confirm="Delete this note?" and Dojo asks before going
   ahead. The text up to the first question mark is the title and the rest is the explanation:
   data-confirm="Archive Sam? They will be marked inactive." The action button is named from the
   title's first words (Delete, Remove, Send…) and is red when it destroys or takes something
   away; data-confirm-button and data-confirm-tone ("danger" or "primary") override that.

   Scripts can ask too: await window.dojoConfirm({title, message, button, tone}) is true or false. */
(function () {
    'use strict';

    // [how the title starts, action button, tone, icon]
    const ACTIONS = [
        ['permanently erase', 'Erase', 'danger', 'bi-exclamation-octagon'],
        ['delete', 'Delete', 'danger', 'bi-trash3'],
        ['remove', 'Remove', 'danger', 'bi-trash3'],
        ['archive', 'Archive', 'danger', 'bi-archive'],
        ['reject', 'Reject', 'danger', 'bi-x-circle'],
        ['lock', 'Lock out', 'danger', 'bi-lock'],
        ['reset', 'Reset', 'danger', 'bi-arrow-counterclockwise'],
        ['go back', 'Reset', 'danger', 'bi-arrow-counterclockwise'],
        ['turn off', 'Turn off', 'danger', 'bi-toggle-off'],
        ['take', 'Take out', 'danger', 'bi-person-dash'],
        ['leave', 'Leave', 'danger', 'bi-box-arrow-right'],
        ['cancel', 'Yes, cancel', 'danger', 'bi-x-circle'],
        ['replace', 'Replace', 'danger', 'bi-arrow-repeat'],
        ['send', 'Send', 'primary', 'bi-send'],
        ['reactivate', 'Reactivate', 'primary', 'bi-arrow-repeat'],
        ['assign', 'Assign', 'primary', 'bi-award'],
        ['switch', 'Switch', 'primary', 'bi-arrow-left-right'],
        ['let', 'Let them know', 'primary', 'bi-chat-left-text'],
    ];
    const FALLBACK = ['', 'Continue', 'primary', 'bi-question-circle'];
    const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── What to ask ─────────────────────────────────────────────────────
    function options(source) {
        const opts = typeof source === 'string' ? { text: source } : { ...source };
        if (!opts.title) {
            const text = (opts.text || '').trim();
            const end = text.indexOf('?');
            opts.title = end === -1 ? text : text.slice(0, end + 1);
            opts.message = opts.message || (end === -1 ? '' : text.slice(end + 1).trim());
        }
        const start = opts.title.toLowerCase();
        const [, button, tone, icon] = ACTIONS.find(([words]) => start.startsWith(words + ' ')) || FALLBACK;
        opts.button = opts.button || button;
        opts.tone = opts.tone === 'danger' || opts.tone === 'primary' ? opts.tone : tone;
        opts.icon = opts.icon || (opts.tone === 'danger' && icon === FALLBACK[3] ? 'bi-exclamation-triangle' : icon);
        opts.dismiss = opts.dismiss || (start.startsWith('cancel ') ? 'Don’t cancel' : 'Cancel');
        return opts;
    }

    function optionsFrom(el) {
        const d = el.dataset;
        return options({
            text: d.confirm, button: d.confirmButton, tone: d.confirmTone, dismiss: d.confirmDismiss,
        });
    }

    // ── The dialog ──────────────────────────────────────────────────────
    let dialog = null;
    let settle = null;      // resolves the open question

    function build() {
        dialog = document.createElement('dialog');
        dialog.className = 'dojo-confirm';
        dialog.setAttribute('role', 'alertdialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'dojoConfirmTitle');
        dialog.setAttribute('aria-describedby', 'dojoConfirmMessage');
        dialog.innerHTML =
            '<div class="dc-panel">' +
            '<div class="dc-icon" aria-hidden="true"><i class="bi"></i></div>' +
            '<h2 class="dc-title" id="dojoConfirmTitle"></h2>' +
            '<p class="dc-message" id="dojoConfirmMessage"></p>' +
            '<div class="dc-actions">' +
            '<button type="button" class="dc-btn dc-dismiss"></button>' +
            '<button type="button" class="dc-btn dc-action"></button>' +
            '</div></div>';
        document.body.appendChild(dialog);

        dialog.querySelector('.dc-dismiss').addEventListener('click', () => finish(false));
        dialog.querySelector('.dc-action').addEventListener('click', () => finish(true));
        dialog.addEventListener('cancel', (e) => {      // the Escape key
            e.preventDefault();
            finish(false);
        });
        // Like an alert on a phone, a tap outside doesn't answer the question: the box nudges instead.
        dialog.addEventListener('click', (e) => {
            if (e.target !== dialog || reducedMotion()) return;
            const panel = dialog.querySelector('.dc-panel');
            panel.classList.remove('dc-nudge');
            void panel.offsetWidth;
            panel.classList.add('dc-nudge');
        });
    }

    function ask(opts) {
        if (typeof HTMLDialogElement === 'undefined') {
            return Promise.resolve(window.confirm([opts.title, opts.message].filter(Boolean).join('\n\n')));
        }
        if (!dialog) build();
        if (settle) settle(false);               // a new question replaces an unanswered one

        dialog.className = `dojo-confirm dc-${opts.tone}`;
        dialog.querySelector('.dc-icon i').className = `bi ${opts.icon}`;
        dialog.querySelector('.dc-title').textContent = opts.title;
        const message = dialog.querySelector('.dc-message');
        message.textContent = opts.message || '';
        message.hidden = !opts.message;
        if (!opts.message) dialog.removeAttribute('aria-describedby');
        else dialog.setAttribute('aria-describedby', 'dojoConfirmMessage');
        const action = dialog.querySelector('.dc-action');
        const dismiss = dialog.querySelector('.dc-dismiss');
        action.textContent = opts.button;
        dismiss.textContent = opts.dismiss;

        if (!dialog.open) dialog.showModal();
        // Going ahead is never the default for something that can't be taken back.
        (opts.tone === 'danger' ? dismiss : action).focus();

        return new Promise((resolve) => {
            settle = (answer) => {
                settle = null;
                resolve(answer);
            };
        });
    }

    function finish(answer) {
        if (!settle || dialog.classList.contains('dc-closing')) return;
        const done = settle;
        if (reducedMotion()) {
            dialog.close();
            done(answer);
            return;
        }
        dialog.classList.add('dc-closing');
        let closed = false;
        const close = () => {
            if (closed) return;
            closed = true;
            dialog.classList.remove('dc-closing');
            dialog.close();
            done(answer);
        };
        dialog.querySelector('.dc-panel').addEventListener('animationend', close, { once: true });
        setTimeout(close, 250);      // in case the animation doesn't run
    }

    window.dojoConfirm = (opts) => ask(options(opts || {}));

    // ── Asking before buttons, links and forms go ahead ─────────────────
    let goAhead = null;     // the element whose next click or submit has been confirmed

    document.addEventListener('click', (e) => {
        const el = e.target.closest('[data-confirm]');
        if (!el || el.tagName === 'FORM' || el.disabled) return;
        if (goAhead === el) {
            goAhead = null;
            return;
        }
        e.preventDefault();
        e.stopImmediatePropagation();
        ask(optionsFrom(el)).then((ok) => {
            if (!ok) return;
            goAhead = el;
            el.click();
        });
    }, true);

    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-confirm')) return;
        if (goAhead === form) {
            goAhead = null;
            return;
        }
        e.preventDefault();
        e.stopImmediatePropagation();
        const submitter = e.submitter;
        ask(optionsFrom(form)).then((ok) => {
            if (!ok) return;
            goAhead = form;
            if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
            else form.submit();
        });
    }, true);
})();
