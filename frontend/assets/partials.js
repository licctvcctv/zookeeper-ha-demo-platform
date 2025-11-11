export async function loadPartials(root = document) {
  if (!root || typeof root.querySelectorAll !== 'function') {
    return;
  }
  const containers = Array.from(root.querySelectorAll('[data-include]'));
  for (const container of containers) {
    const url = container.getAttribute('data-include');
    if (!url) continue;
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const html = await response.text();
      const template = document.createElement('template');
      template.innerHTML = html.trim();
      await loadPartials(template.content);
      container.replaceWith(template.content.cloneNode(true));
    } catch (error) {
      console.error(`Failed to load partial ${url}`, error);
      const fallback = document.createElement('section');
      fallback.className = 'panel';
      const message = document.createElement('p');
      message.className = 'muted';
      message.textContent = `无法加载 ${url}：${error.message}`;
      fallback.appendChild(message);
      container.replaceWith(fallback);
    }
  }
}
