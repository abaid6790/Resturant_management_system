(function () {
  // Confirmation dialog for any element with data-confirm (buttons inside forms, links).
  const dlg = document.getElementById('confirm-dialog');
  function ask(message, okLabel) {
    return new Promise(function (resolve) {
      dlg.querySelector('[data-msg]').textContent = message;
      dlg.querySelector('[value=ok]').textContent = okLabel || 'Confirm';
      dlg.returnValue = '';
      dlg.addEventListener('close', function () { resolve(dlg.returnValue === 'ok'); }, { once: true });
      dlg.showModal();
    });
  }
  document.addEventListener('click', async function (e) {
    const el = e.target.closest('[data-confirm]');
    if (!el || el.dataset.confirmed || !dlg) return;
    e.preventDefault();
    if (await ask(el.dataset.confirm, el.dataset.confirmLabel)) {
      el.dataset.confirmed = '1'; el.click(); delete el.dataset.confirmed;
    }
  });

  // Toasts: success/info fade after 5s, errors stay until dismissed.
  document.querySelectorAll('.toast').forEach(function (t) {
    t.querySelector('button').addEventListener('click', function () { t.remove(); });
    if (!t.classList.contains('error')) setTimeout(function () { t.remove(); }, 5000);
  });

  // Mobile navigation.
  const menu = document.querySelector('.menu-btn');
  if (menu) menu.addEventListener('click', function () { document.body.classList.toggle('nav-open'); });

  // "/" focuses the first search box.
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      const s = document.querySelector('input[type=search]');
      if (s) { e.preventDefault(); s.focus(); }
    }
  });

  // Prevent double submits.
  document.addEventListener('submit', function (e) {
    const b = e.target.querySelector('button[type=submit].primary');
    if (b) setTimeout(function () { b.disabled = true; }, 0);
  });
})();
