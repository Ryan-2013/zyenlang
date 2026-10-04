const tabs = [...document.querySelectorAll('[data-panel]')];
const panels = [...document.querySelectorAll('[data-code-panel]')];

function activatePanel(name) {
  for (const tab of tabs) {
    const active = tab.dataset.panel === name;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
  }

  for (const panel of panels) {
    const active = panel.dataset.codePanel === name;
    panel.classList.toggle('active', active);
    panel.hidden = !active;
  }
}

for (const tab of tabs) {
  tab.addEventListener('click', () => activatePanel(tab.dataset.panel));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const offset = event.key === 'ArrowRight' ? 1 : -1;
    const next = tabs[(tabs.indexOf(tab) + offset + tabs.length) % tabs.length];
    activatePanel(next.dataset.panel);
    next.focus();
  });
}

for (const button of document.querySelectorAll('[data-copy]')) {
  button.addEventListener('click', async () => {
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = 'Copied';
    } catch {
      button.textContent = 'Select';
    }
    window.setTimeout(() => {
      button.textContent = original;
    }, 1400);
  });
}
