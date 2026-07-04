export function renderMarkdown(markdown: string): string {
  const normalized = markdown.replace(/\r\n?/g, '\n')
  const blocks: string[] = []
  let cursor = 0

  for (const match of normalized.matchAll(/```([^\n`]*)\n?([\s\S]*?)```/g)) {
    const matchIndex = match.index ?? 0
    const before = normalized.slice(cursor, matchIndex)
    blocks.push(renderMarkdownBlocks(before))
    const language = sanitizeClassName(match[1]?.trim() ?? '')
    const code = escapeHtml(match[2] ?? '')
    const languageClass = language ? ` class="language-${language}"` : ''
    blocks.push(`<pre><code${languageClass}>${code}</code></pre>`)
    cursor = matchIndex + match[0].length
  }

  blocks.push(renderMarkdownBlocks(normalized.slice(cursor)))
  return blocks.join('')
}

function renderMarkdownBlocks(markdown: string): string {
  const lines = markdown.split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let listItems: string[] = []

  const flushParagraph = (): void => {
    if (!paragraph.length) {
      return
    }
    html.push(`<p>${paragraph.map(renderInlineMarkdown).join('<br>')}</p>`)
    paragraph = []
  }

  const flushList = (): void => {
    if (!listItems.length) {
      return
    }
    html.push(`<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</ul>`)
    listItems = []
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      flushList()
      continue
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`)
      continue
    }

    const listItem = /^[-*]\s+(.+)$/.exec(trimmed)
    if (listItem) {
      flushParagraph()
      listItems.push(listItem[1])
      continue
    }

    if (trimmed.startsWith('> ')) {
      flushParagraph()
      flushList()
      html.push(`<blockquote>${renderInlineMarkdown(trimmed.slice(2))}</blockquote>`)
      continue
    }

    flushList()
    paragraph.push(trimmed)
  }

  flushParagraph()
  flushList()
  return html.join('')
}

function renderInlineMarkdown(text: string): string {
  const codeSegments: string[] = []
  let escaped = escapeHtml(text).replace(/`([^`]+)`/g, (_match, code: string) => {
    const index = codeSegments.push(`<code>${code}</code>`) - 1
    return `\u0000CODE${index}\u0000`
  })

  escaped = escaped
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
    .replace(/_([^_\n]+)_/g, '<em>$1</em>')

  return escaped.replace(/\u0000CODE(\d+)\u0000/g, (_match, index: string) => codeSegments[Number(index)] ?? '')
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function sanitizeClassName(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 32)
}
