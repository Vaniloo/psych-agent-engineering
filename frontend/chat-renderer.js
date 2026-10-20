/* Reply text is untrusted. Parse Markdown, then allow only static document tags. */
(() => {
  const tags = ["p", "br", "strong", "em", "del", "a", "ul", "ol", "li", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr", "table", "thead", "tbody", "tr", "th", "td"];

  function visibleText(text) {
    const value=String(text??'');
    const internal=/<[\s|｜]*DSML|<\/?(?:tool_call|function_call|invoke)\b/i;
    return internal.test(value)?'这条旧回复混入了无效的工具调用内容，不能作为查询结果。可以重新发起查询。':value;
  }

  function render(target, text) {
    text=visibleText(text);
    if (!window.marked || !window.DOMPurify) {
      target.textContent = text;
      return;
    }
    const html = window.marked.parse(String(text), {gfm: true, breaks: true, async: false});
    const fragment = window.DOMPurify.sanitize(html, {
      ALLOWED_TAGS: tags,
      ALLOWED_ATTR: ["href", "title"],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      RETURN_DOM_FRAGMENT: true,
    });
    fragment.querySelectorAll("a").forEach(link => {
      const href = link.getAttribute("href");
      try {
        const url = new URL(href, window.location.href);
        if (!href || !["http:", "https:", "mailto:"].includes(url.protocol)) {
          link.removeAttribute("href");
          return;
        }
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
      } catch {
        link.removeAttribute("href");
      }
    });
    fragment.querySelectorAll("table").forEach(table => {
      const wrapper = document.createElement("div");
      wrapper.className = "markdown-table";
      table.replaceWith(wrapper);
      wrapper.appendChild(table);
    });
    target.replaceChildren(fragment);
  }

  window.PsychChatRenderer = Object.freeze({render,visibleText});
})();
