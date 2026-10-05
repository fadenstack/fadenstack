/**
 * Guide and help texts with a little emphasis: <strong>, <em>, <code> and <kbd>,
 * and a blank line between paragraphs. Nothing else is interpreted, and nothing
 * goes in as HTML: a stray tag shows as text, never as markup.
 */
import { Fragment, type ReactNode } from "react";

const TAG = /<(strong|em|code|kbd)>([\s\S]*?)<\/\1>/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of text.matchAll(TAG)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const [, tag, inner] = match;
    const key = `${tag}-${n++}`;
    if (tag === "strong") out.push(<strong key={key}>{inner}</strong>);
    else if (tag === "em") out.push(<em key={key}>{inner}</em>);
    else if (tag === "kbd") out.push(<kbd key={key}>{inner}</kbd>);
    else out.push(<code key={key}>{inner}</code>);
    last = at + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function RichText({ text, paragraphGap = 1 }: { text: string; paragraphGap?: number }) {
  const paragraphs = text.split(/\n\s*\n/);
  return (
    <>
      {paragraphs.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <span style={{ display: "block", height: `${paragraphGap * 0.5}em` }} />}
          {inline(p)}
        </Fragment>
      ))}
    </>
  );
}
