/*
 * Safe form submit — every POST form on the site is saved in the background instead of by a
 * normal page submit, so iPhone Safari's "The network connection was lost" can't lose a save.
 *
 * Why: Safari (iOS 18+) can fail a form POST on a stale connection and never retries it, and
 * the app answers every save with a redirect, which Safari can also drop. Here the form is
 * sent with fetch(); the server (see app/services/submit_guard.py) answers a save with a small
 * JSON {"safe_redirect": url} instead of a redirect, and the page then simply opens that URL.
 *
 *   - A one-time _submit_token goes with every attempt, so the server never saves twice.
 *   - If the connection drops, the save is retried automatically (4 attempts).
 *   - If it still can't get through, the form stays filled in and a message says so.
 *   - Non-redirect answers are shown as usual: an HTML page replaces the current one,
 *     a file (PDF/Excel) is opened or downloaded.
 *   - "Saving…" on the button; double taps are ignored; Back makes the form usable again.
 *
 * Opt out with data-safe-submit="off". Forms whose own script cancels the submit
 * (e.g. the Job form, or a declined confirm()) are left alone.
 */
(function () {
    'use strict';

    var MAX_ATTEMPTS = 4;
    var PING_URL = '/ping';

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

    function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

    function statusBox(form) {
        var box = form.querySelector('.safe-submit-status');
        if (!box) {
            box = document.createElement('div');
            box.className = 'safe-submit-status';
            box.setAttribute('role', 'alert');
            var buttons = submitButtons(form);
            var anchor = buttons.length ? buttons[buttons.length - 1] : null;
            if (anchor && anchor.parentNode) {
                anchor.parentNode.insertBefore(box, anchor.nextSibling);
            } else {
                form.appendChild(box);
            }
        }
        return box;
    }

    function showStatus(form, kind, text) {
        var box = statusBox(form);
        box.className = 'safe-submit-status safe-submit-' + kind;
        box.textContent = text;
    }

    function clearStatus(form) {
        var box = form.querySelector('.safe-submit-status');
        if (box) { box.parentNode.removeChild(box); }
    }

    function lock(form, submitter) {
        form.setAttribute('data-safe-submitting', '1');
        Array.prototype.forEach.call(submitButtons(form), function (button) {
            if (!button.hasAttribute('data-safe-was-disabled')) {
                button.setAttribute('data-safe-was-disabled', button.disabled ? '1' : '0');
            }
            button.disabled = true;
        });
        if (submitter && !submitter.hasAttribute('data-safe-label')) {
            if (submitter.tagName === 'BUTTON') {
                submitter.setAttribute('data-safe-label', submitter.innerHTML);
                submitter.textContent = 'Saving…';
            } else if (submitter.tagName === 'INPUT') {
                submitter.setAttribute('data-safe-label', submitter.value);
                submitter.value = 'Saving…';
            }
        }
    }

    function unlock(form) {
        form.removeAttribute('data-safe-submitting');
        Array.prototype.forEach.call(form.querySelectorAll('[data-safe-was-disabled]'), function (button) {
            button.disabled = button.getAttribute('data-safe-was-disabled') === '1';
            button.removeAttribute('data-safe-was-disabled');
            var label = button.getAttribute('data-safe-label');
            if (label !== null) {
                if (button.tagName === 'INPUT') { button.value = label; } else { button.innerHTML = label; }
                button.removeAttribute('data-safe-label');
            }
        });
    }

    function filenameFrom(disposition) {
        if (!disposition) { return ''; }
        var star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition);
        if (star) { try { return decodeURIComponent(star[1].replace(/"/g, '')); } catch (e) { /* fall through */ } }
        var plain = /filename="?([^";]+)"?/i.exec(disposition);
        return plain ? plain[1] : '';
    }

    function showHtml(html, url) {
        document.open();
        document.write(html);
        document.close();
        if (url && window.history && window.history.replaceState) {
            try { window.history.replaceState(null, '', url); } catch (e) { /* ignore */ }
        }
    }

    function handleResponse(form, response) {
        var type = (response.headers.get('Content-Type') || '').toLowerCase();

        if (type.indexOf('application/json') !== -1) {
            return response.json().then(function (data) {
                if (data && data.safe_redirect) {
                    window.location.assign(data.safe_redirect);
                    return;
                }
                // Some other JSON answer (not expected from a form) — show it plainly.
                showHtml('<pre>' + JSON.stringify(data, null, 2).replace(/</g, '&lt;') + '</pre>');
            });
        }

        if (type.indexOf('text/html') !== -1 || type === '') {
            return response.text().then(function (html) {
                showHtml(html, response.redirected ? response.url : null);
            });
        }

        // A file (PDF, Excel, image): open it, or download it when the server asks for that.
        return response.blob().then(function (blob) {
            var url = URL.createObjectURL(blob);
            var name = filenameFrom(response.headers.get('Content-Disposition'));
            var attachment = /attachment/i.test(response.headers.get('Content-Disposition') || '');
            if (attachment && name) {
                var link = document.createElement('a');
                link.href = url;
                link.download = name;
                document.body.appendChild(link);
                link.click();
                link.parentNode.removeChild(link);
                unlock(form);
                clearStatus(form);
            } else {
                window.location.assign(url);
            }
        });
    }

    function actionUrl(form) {
        // Read the attribute: many forms have a hidden field named "action", which hides form.action.
        var attr = form.getAttribute('action');
        if (!attr) { return window.location.href; }
        try { return new URL(attr, window.location.href).href; } catch (e) { return attr; }
    }

    function send(form, body) {
        return fetch(actionUrl(form), {
            method: 'POST',
            body: body,
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { 'X-Safe-Submit': '1', 'Accept': 'application/json, text/html;q=0.9, */*;q=0.8' }
        });
    }

    function submitInBackground(form, submitter) {
        if (!form.querySelector('input[name="_submit_token"]')) {
            hidden(form, '_submit_token', newToken());
        }
        var body = new FormData(form);
        if (submitter && submitter.name) {
            body.append(submitter.name, submitter.value || '');
        }
        lock(form, submitter);
        clearStatus(form);

        var attempt = 0;
        function tryOnce() {
            attempt += 1;
            return send(form, body).then(function (response) {
                return handleResponse(form, response);
            }, function () {
                if (attempt < MAX_ATTEMPTS) {
                    showStatus(form, 'info', 'Connection dropped — trying again (' + (attempt + 1) + ' of ' + MAX_ATTEMPTS + ')…');
                    // A quick GET lets Safari open a fresh connection before the next try.
                    return wait(800 * attempt)
                        .then(function () { return fetch(PING_URL + '?t=' + Date.now(), { cache: 'no-store', credentials: 'same-origin' }).catch(function () {}); })
                        .then(tryOnce);
                }
                unlock(form);
                showStatus(form, 'error', 'Could not reach the server, so nothing was saved yet. Your entries are still here — check your internet and tap the button again.');
            });
        }

        // Warm the connection first, then save.
        return fetch(PING_URL + '?t=' + Date.now(), { cache: 'no-store', credentials: 'same-origin' })
            .catch(function () {})
            .then(tryOnce)
            .catch(function () {
                unlock(form);
                showStatus(form, 'error', 'Something went wrong while saving. Your entries are still here — please try again.');
            });
    }

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) { return; }
        if (event.defaultPrevented) { return; }                       // page script or declined confirm()
        if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') { return; }
        if (form.getAttribute('data-safe-submit') === 'off') { return; }
        var target = (form.getAttribute('target') || '').toLowerCase();
        if (target && target !== '_self') { return; }                  // new-tab forms: leave as they are
        if (!window.fetch || !window.FormData || !window.Promise) { return; }  // very old browser

        event.preventDefault();
        if (form.getAttribute('data-safe-submitting') === '1') { return; }   // double tap
        submitInBackground(form, event.submitter || null);
    });

    // Coming back to the page with Back: make the form usable again.
    window.addEventListener('pageshow', function () {
        Array.prototype.forEach.call(document.querySelectorAll('form[data-safe-submitting="1"]'), function (form) {
            unlock(form);
            clearStatus(form);
        });
    });
})();
