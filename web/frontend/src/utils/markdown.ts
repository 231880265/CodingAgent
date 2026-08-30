import DOMPurify from "dompurify";
import { Marked } from "marked";

const markdown = new Marked({
  breaks: false,
  gfm: true,
  pedantic: false,
});

export function renderMarkdown(source: string): string {
  if (!source.trim()) return "";

  const rendered = markdown.parse(source, { async: false }) as string;
  const clean = DOMPurify.sanitize(rendered, {
    USE_PROFILES: { html: true },
  });
  const template = document.createElement("template");
  template.innerHTML = clean;

  template.content.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });

  template.content.querySelectorAll<HTMLTableElement>("table").forEach((table) => {
    const wrapper = document.createElement("div");
    wrapper.className = "markdown-table-scroll";
    table.parentNode?.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });

  return template.innerHTML;
}
