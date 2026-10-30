/*
 * Safe form submit — works around a Safari (iOS 18+) bug where a form POST is sent on a
 * connection the server already closed and fails with "The network connection was lost".
 * Safari retries GET requests on a fresh connection but never retries POSTs.
 *
 * For every POST form on the site:
 *   1. Just before submitting, a tiny GET /ping is made. If the open connection is stale,
 *      Safari retries the GET on a fresh connection, so the form POST that follows
 *      goes over a live connection.
 *   2. A one-time _submit_token is added so the server ignores accidental double submits
 *      or re-submits of the same form (it redirects to the already-saved result instead).
 *   3. The button shows "Saving…" and double taps are ignored. If the page is shown again
 *      (Back button after an error), the buttons are re-enabled.
 *
 * A form can opt out with data-safe-submit="off". Forms whose own script cancels the
 * submit (e.g. the Job form, or a confirm() that is declined) are left alone.
 */
(function () {
    'use strict';

    var PING_URL = '/ping';
    var PING_TIMEOUT_MS = 6000;

    function newToken() {
        var bytes = new Uint8Array(16);
        var cryptoObj = window.crypto || window.msCrypto;
        if (cryptoObj && cryptoObj.getRandomValues) {
            cryptoObj.getRandomValues(bytes);
        } else {
            for (var i = 0; i < bytes.length; i++) { bytes[i] = Math.floor(Math.random() * 256); }
        }
        return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
    }

    function hidden(form, name, value, marker) {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = value;
        if (marker) { input.setAttribute(marker, '1'); }
        form.appendChild(input);
        return input;
    }

    function submitButtons(form) {
        return form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]');
    }

    function nativeSubmit(form) {
        // Use the prototype method in case a field is named "submit".
        HTMLFormElement.prototype.submit.call(form);
    }

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) { return; }
        if (event.defaultPrevented) { return; }                       // page script or declined confirm()
        if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') { return; }
        if (form.getAttribute('data-safe-submit') === 'off') { return; }
        var target = (form.getAttribute('target') || '').toLowerCase();
        if (target && target !== '_self') { return; }                  // new-tab forms: leave as they are
        if (!window.fetch) { return; }                                // very old browser: normal submit

        event.preventDefault();
        if (form.getAttribute('data-safe-submitting') === '1') { return; }   // double tap
        form.setAttribute('data-safe-submitting', '1');

        if (!form.querySelector('input[name="_submit_token"]')) {
            hidden(form, '_submit_token', newToken());
        }
        var submitter = event.submitter;
        if (submitter && submitter.name) {
            hidden(form, submitter.name, submitter.value || '', 'data-safe-submitter');
        }
        Array.prototype.forEach.call(submitButtons(form), function (button) {
            button.setAttribute('data-safe-was-disabled', button.disabled ? '1' : '0');
            button.disabled = true;
        });
        if (submitter && submitter.tagName === 'BUTTON') {
            submitter.setAttribute('data-safe-label', submitter.innerHTML);
            submitter.textContent = 'Saving…';
        } else if (submitter && submitter.tagName === 'INPUT') {
            submitter.setAttribute('data-safe-label', submitter.value);
            submitter.value = 'Saving…';
        }

        var sent = false;
        function go() {
            if (sent) { return; }
            sent = true;
            nativeSubmit(form);
        }

        try {
            var controller = window.AbortController ? new AbortController() : null;
            setTimeout(function () {
                if (controller) { controller.abort(); }
                go();
            }, PING_TIMEOUT_MS);
            fetch(PING_URL + '?t=' + Date.now(), {
                method: 'GET',
                cache: 'no-store',
                credentials: 'same-origin',
                signal: controller ? controller.signal : undefined
            }).then(go, go);
        } catch (err) {
            go();
        }
    });

    // Coming back to the page (Back button after an error page): make the form usable again.
    window.addEventListener('pageshow', function () {
        Array.prototype.forEach.call(document.querySelectorAll('form[data-safe-submitting="1"]'), function (form) {
            form.removeAttribute('data-safe-submitting');
            Array.prototype.forEach.call(form.querySelectorAll('[data-safe-submitter]'), function (node) {
                node.parentNode.removeChild(node);
            });
            Array.prototype.forEach.call(form.querySelectorAll('[data-safe-was-disabled]'), function (button) {
                button.disabled = button.getAttribute('data-safe-was-disabled') === '1';
                button.removeAttribute('data-safe-was-disabled');
                var label = button.getAttribute('data-safe-label');
                if (label !== null) {
                    if (button.tagName === 'INPUT') { button.value = label; } else { button.innerHTML = label; }
                    button.removeAttribute('data-safe-label');
                }
            });
        });
    });
})();
