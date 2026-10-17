import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { escapeHtml, highlightCode } from './highlight'

export const MAX_MARKDOWN_CHARACTERS = 150_000

/** Only explicit web/mail links, fragments and ordinary relative document paths. */
export function safeMarkdownLink(value: string): boolean {
  if (!value || /[\u0000-\u0020\u007f-\u009f\\]/.test(value) || value.startsWith('//')) return false
  if (/^https?:\/\//i.test(value)) {
    try {
      const url = new URL(value)
      return Boolean(url.hostname) && !url.username && !url.password
    } catch {
      return false
    }
  }
  if (/^mailto:[^?<>]+$/i.test(value)) return true
  return !/^[^/#?]*:/.test(value)
}

const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
  breaks: false,
  maxNesting: 30,
  highlight: (code, language) => highlightCode(code, language).html,
})
markdown.validateLink = safeMarkdownLink
markdown.renderer.rules.image = (tokens, index) => {
  const alt = tokens[index]?.content ?? ''
  return `<span class="markdown-image">${escapeHtml(alt || '[image]')}</span>`
}
markdown.renderer.rules.link_open = (tokens, index, options, _environment, renderer) => {
  const token = tokens[index]!
  const href = String(token.attrGet('href') ?? '')
  if (!safeMarkdownLink(href)) token.attrSet('href', '#')
  token.attrSet('rel', 'noopener noreferrer nofollow')
  if (/^https?:\/\//i.test(href)) token.attrSet('target', '_blank')
  return renderer.renderToken(tokens, index, options)
}
markdown.renderer.rules.fence = (tokens, index) => {
  const token = tokens[index]!
  const language = token.info.trim().split(/\s+/)[0]
  const code = highlightCode(token.content, language)
  return `<pre class="markdown-code"><code class="hljs language-${code.language}">${code.html}</code></pre>\n`
}

/** The parser cannot emit project HTML/images; sanitizer is a second browser boundary. */
export function renderMarkdown(content: string): { html: string; truncated: boolean } {
  const text = content.slice(0, MAX_MARKDOWN_CHARACTERS)
  const html = markdown.render(text)
  // SSR has no DOM. Its output still comes exclusively from our HTML-disabled
  // parser, safe link renderer and the escaping highlighter above.
  const sanitized = DOMPurify.isSupported
    ? DOMPurify.sanitize(html, {
        ALLOWED_TAGS: [
          'p',
          'h1',
          'h2',
          'h3',
          'h4',
          'h5',
          'h6',
          'ul',
          'ol',
          'li',
          'blockquote',
          'pre',
          'code',
          'span',
          'strong',
          'em',
          's',
          'del',
          'a',
          'hr',
          'br',
          'table',
          'thead',
          'tbody',
          'tr',
          'th',
          'td',
        ],
        ALLOWED_ATTR: ['href', 'title', 'class', 'rel', 'target', 'start'],
        ALLOW_DATA_ATTR: false,
        ALLOW_ARIA_ATTR: false,
        ALLOWED_URI_REGEXP: /^(?:https?:\/\/|mailto:|[?#]|\/(?!\/)|\.{1,2}\/|[^\s:/?#]+(?:\/|$))/i,
      })
    : html
  return { html: sanitized, truncated: content.length > text.length }
}
