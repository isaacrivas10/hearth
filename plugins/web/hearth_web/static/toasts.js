(function () {
    var _density = document.documentElement.dataset.density || 'balanced'
    var _duration = { compact: 2500, balanced: 4000, airy: 6000 }[_density] || 4000

    var _notyf = new Notyf({
        duration: _duration,
        position: { x: 'right', y: 'bottom' },
        dismissible: true,
        types: [
            { type: 'warning', background: 'var(--color-warning)', icon: false },
            { type: 'info',    background: 'var(--color-muted)',   icon: false }
        ]
    })

    window.showToast = function (message, variant) {
        if (variant === 'success') return _notyf.success(message)
        if (variant === 'danger')  return _notyf.error(message)
        if (variant === 'warning') return _notyf.open({ type: 'warning', message: message })
        return _notyf.open({ type: 'info', message: message })
    }

    document.addEventListener('htmx:afterOnLoad', function (e) {
        var xhr = e.detail.xhr
        if (!xhr) return
        var header = xhr.getResponseHeader('HX-Trigger')
        if (!header) return
        try {
            var triggers = JSON.parse(header)
            if (triggers.showToast) {
                window.showToast(triggers.showToast.message, triggers.showToast.variant)
            }
            if (triggers.closeModal) {
                window.dispatchEvent(new Event('close-modal'))
            }
        } catch (_) {}
    })
})()
