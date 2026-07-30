(function () {
    'use strict';

    var entries = [];
    var panelOpen = false;
    var badge, panel, panelBody;

    function addEntry(kind, summary, detail) {
        entries.unshift({ kind: kind, summary: summary, detail: detail || '', time: new Date() });
        if (entries.length > 50) entries.length = 50;
        render();
    }

    function colorFor(kind) {
        if (kind.indexOf('htmx') === 0) return '#f97316';
        return '#ef4444';
    }

    function escapeHtml(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function render() {
        badge.textContent = entries.length;
        badge.style.display = entries.length ? 'flex' : 'none';
        if (panelOpen) renderPanel();
    }

    function renderPanel() {
        panelBody.innerHTML = entries.length ? entries.map(function (e) {
            return (
                '<div style="border-bottom:1px solid #1e3a5f;padding:10px 12px;font-size:12.5px;">' +
                    '<div style="display:flex;justify-content:space-between;gap:8px;">' +
                        '<span style="font-weight:600;color:' + colorFor(e.kind) + ';">' + escapeHtml(e.kind) + '</span>' +
                        '<span style="color:#8ca3bd;">' + e.time.toTimeString().slice(0, 8) + '</span>' +
                    '</div>' +
                    '<div style="color:#e2e8f0;margin-top:2px;word-break:break-word;">' + escapeHtml(e.summary) + '</div>' +
                    (e.detail ? '<div style="color:#8ca3bd;margin-top:4px;word-break:break-word;font-family:monospace;font-size:11px;white-space:pre-wrap;">' + escapeHtml(e.detail) + '</div>' : '') +
                '</div>'
            );
        }).join('') : '<div style="padding:16px;color:#8ca3bd;font-size:13px;">No silent failures captured yet.</div>';
    }

    function togglePanel() {
        panelOpen = !panelOpen;
        panel.style.display = panelOpen ? 'block' : 'none';
        if (panelOpen) renderPanel();
    }

    function build() {
        var wrap = document.createElement('div');
        wrap.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:99999;font-family:system-ui,sans-serif;';

        panel = document.createElement('div');
        panel.style.cssText =
            'display:none;position:absolute;bottom:48px;right:0;width:360px;max-height:60vh;overflow-y:auto;' +
            'background:#0f2139;border:1px solid #1e3a5f;border-radius:10px;box-shadow:0 10px 40px rgba(0,0,0,.45);';

        var header = document.createElement('div');
        header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:10px 12px;border-bottom:1px solid #1e3a5f;position:sticky;top:0;background:#0f2139;border-radius:10px 10px 0 0;';
        header.innerHTML = '<strong style="color:#fff;font-size:13px;">Diagnostics <span style="color:#8ca3bd;font-weight:400;">— silent failures</span></strong>';

        var clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.textContent = 'Clear';
        clearBtn.style.cssText = 'background:none;border:none;color:#8ca3bd;font-size:12px;cursor:pointer;';
        clearBtn.addEventListener('click', function () {
            entries = [];
            render();
        });
        header.appendChild(clearBtn);

        panelBody = document.createElement('div');

        panel.appendChild(header);
        panel.appendChild(panelBody);

        badge = document.createElement('button');
        badge.type = 'button';
        badge.title = 'Dojo diagnostics — click to view silent failures on this page';
        badge.style.cssText =
            'display:none;align-items:center;justify-content:center;width:40px;height:40px;border-radius:50%;' +
            'background:#dc2626;color:#fff;font-weight:700;font-size:14px;border:none;cursor:pointer;' +
            'box-shadow:0 4px 14px rgba(0,0,0,.35);';
        badge.addEventListener('click', togglePanel);

        wrap.appendChild(panel);
        wrap.appendChild(badge);
        document.body.appendChild(wrap);
    }

    function summarizeRequest(evt) {
        var xhr = evt.detail && evt.detail.xhr;
        var target = evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.path;
        var verb = evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.verb;
        var status = xhr ? xhr.status : '';
        return (verb ? verb.toUpperCase() : 'REQUEST') + ' ' + (target || '') + (status ? ' → ' + status : '');
    }

    document.addEventListener('DOMContentLoaded', function () {
        build();

        // htmx requests that fail don't swap content or show anything by
        // default — these are exactly the "silent failure" case.
        document.body.addEventListener('htmx:responseError', function (evt) {
            var xhr = evt.detail && evt.detail.xhr;
            addEntry('htmx:responseError', summarizeRequest(evt), xhr ? xhr.responseText.slice(0, 500) : '');
        });
        document.body.addEventListener('htmx:sendError', function (evt) {
            addEntry('htmx:sendError', summarizeRequest(evt), 'Network error — request never reached the server.');
        });
        document.body.addEventListener('htmx:swapError', function (evt) {
            addEntry('htmx:swapError', summarizeRequest(evt), 'Response received but the page failed to update.');
        });
        document.body.addEventListener('htmx:timeout', function (evt) {
            addEntry('htmx:timeout', summarizeRequest(evt), 'Request timed out.');
        });

        // Uncaught JS errors and unhandled promise rejections.
        window.addEventListener('error', function (evt) {
            if (evt.error) {
                addEntry('js error', evt.message, (evt.filename || '') + ':' + evt.lineno);
            }
        });
        window.addEventListener('unhandledrejection', function (evt) {
            addEntry('promise rejection', String(evt.reason && evt.reason.message || evt.reason), '');
        });
    });
})();
