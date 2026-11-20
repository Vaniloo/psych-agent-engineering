(() => {
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
  window.escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => entities[char]);
})();
