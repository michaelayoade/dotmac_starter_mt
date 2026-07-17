/**
 * Alpine.js CSP-compatible component registrations.
 *
 * All Alpine logic lives here as named Alpine.data() components.
 * Templates reference them by name: x-data="componentName"
 * Server data is passed via data-* attributes, parsed in init().
 *
 * Ported from ST:static/js/components.js — trimmed to the components this
 * app's shell actually uses (dark mode store, toast store, user menu,
 * mobile sidebar toggle). Donor components tied to features this template
 * doesn't have (notificationBell — no notifications feature; demoCounter —
 * no marketing index page; brandingEditor/fileUploadZone/fileDropZone — no
 * branding-editor or file-upload UI yet) were dropped rather than carried
 * as dead code referencing endpoints that don't exist here. Re-add from the
 * donor file if/when the corresponding feature ships.
 */
document.addEventListener('alpine:init', function () {

    // ── Dark Mode (global store, accessed via $store.dark) ───────────
    Alpine.store('dark', {
        on: localStorage.getItem('darkMode') === 'true',
        toggle: function () {
            this.on = !this.on;
            localStorage.setItem('darkMode', String(this.on));
        },
        isOff: function () {
            return !this.on;
        }
    });

    // ── Toast Store (used in base.html toast container) ──────────────
    Alpine.data('toastStore', function () {
        return {
            toasts: [],
            addToast: function (detail) {
                var self = this;
                var id = Date.now();
                this.toasts.push({
                    id: id,
                    message: detail.message,
                    type: detail.type || 'info',
                    visible: true
                });
                setTimeout(function () { self.removeToast(id); }, detail.duration || 4000);
            },
            removeToast: function (id) {
                var self = this;
                var toast = this.toasts.find(function (t) { return t.id === id; });
                if (toast) {
                    toast.visible = false;
                    setTimeout(function () {
                        self.toasts = self.toasts.filter(function (t) { return t.id !== id; });
                    }, 300);
                }
            },
            isSuccess: function (toast) { return toast.type === 'success'; },
            isError: function (toast) { return toast.type === 'error'; },
            isWarning: function (toast) { return toast.type === 'warning'; },
            isInfo: function (toast) { return toast.type === 'info'; }
        };
    });

    // ── User Menu dropdown (used in admin topbar) ────────────────────
    Alpine.data('userMenu', function () {
        return {
            open: false,
            toggle: function () { this.open = !this.open; },
            close: function () { this.open = false; }
        };
    });

    // ── Sidebar Toggle (used in layouts/admin.html mobile overlay) ───
    Alpine.data('sidebarToggle', function () {
        return {
            open: false,
            toggle: function () { this.open = !this.open; },
            close: function () { this.open = false; }
        };
    });

});
