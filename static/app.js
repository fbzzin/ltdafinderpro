// Minimal JS helpers for the LTDAFinder Pro design kit.
// Vanilla JS, no dependency. Include with:
//   <script src="{{ url_for('static', filename='app.js') }}" defer></script>

(function () {
  // ---- Mobile sidebar toggle ----
  const toggle   = document.querySelector('[data-sidebar-toggle]');
  const sidebar  = document.querySelector('.sidebar');
  const backdrop = document.querySelector('.sidebar-backdrop');

  function closeSidebar() {
    sidebar && sidebar.classList.remove('is-open');
    backdrop && backdrop.classList.remove('is-open');
  }
  function openSidebar() {
    sidebar && sidebar.classList.add('is-open');
    backdrop && backdrop.classList.add('is-open');
  }
  toggle && toggle.addEventListener('click', () => {
    sidebar.classList.contains('is-open') ? closeSidebar() : openSidebar();
  });
  backdrop && backdrop.addEventListener('click', closeSidebar);

  // ---- Modals ----
  document.querySelectorAll('[data-modal-open]').forEach(btn => {
    btn.addEventListener('click', () => {
      const sel = btn.getAttribute('data-modal-open');
      const m = document.querySelector(sel);
      m && m.classList.add('is-open');
    });
  });
  document.querySelectorAll('[data-modal-close]').forEach(btn => {
    btn.addEventListener('click', () => {
      const m = btn.closest('.modal-backdrop');
      m && m.classList.remove('is-open');
    });
  });
  document.querySelectorAll('.modal-backdrop').forEach(bd => {
    bd.addEventListener('click', (e) => {
      if (e.target === bd) bd.classList.remove('is-open');
    });
  });

  // ---- Template card selection (gerador de sites) ----
  document.querySelectorAll('[data-template-group]').forEach(group => {
    group.querySelectorAll('.template-card').forEach(card => {
      card.addEventListener('click', () => {
        group.querySelectorAll('.template-card').forEach(c => c.classList.remove('is-selected'));
        card.classList.add('is-selected');
        const input = group.querySelector('input[type="hidden"][data-template-input]');
        if (input) input.value = card.getAttribute('data-template-value') || '';
      });
    });
  });

  // ---- Auto-dismiss flash / toast after 5s ----
  document.querySelectorAll('.toast[data-auto-dismiss]').forEach(t => {
    setTimeout(() => t.remove(), 5000);
  });
})();
