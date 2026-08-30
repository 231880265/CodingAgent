// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders the structures used by long technical answers", () => {
    const html = renderMarkdown(`## Java 与 C++

普通段落包含 **重点**、*强调* 和 \`inlineCode()\`。

- Java 使用虚拟机
- C++ 原生编译

> 这是取舍，不是绝对优劣。

| 维度 | Java | C++ |
| --- | --- | --- |
| 内存 | GC | RAII |

\`\`\`java
class Demo {
  void run() {}
}
\`\`\`

[参考资料](https://example.com)
`);

    expect(html).toContain("<h2>Java 与 C++</h2>");
    expect(html).toContain("<strong>重点</strong>");
    expect(html).toContain("<em>强调</em>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain('class="markdown-table-scroll"');
    expect(html).toContain('<code class="language-java">');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("keeps a single Markdown newline as natural text flow", () => {
    const html = renderMarkdown("第一行\n第二行\n\n新段落");
    expect(html).not.toContain("<br>");
    expect(html.match(/<p>/g)).toHaveLength(2);
  });

  it("sanitizes model-provided HTML before rendering", () => {
    const html = renderMarkdown('<img src="x" onerror="alert(1)"><script>alert(1)</script>');
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("<script");
  });
});
