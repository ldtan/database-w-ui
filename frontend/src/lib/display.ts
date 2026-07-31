/**
 * The visible words of a rich text cell, with the markup taken out.
 *
 * For places that must not render HTML: a grid cell, a card field, a lookup
 * option's label. It is *not* a security measure — the backend sanitises on
 * write, and that is what makes rendering safe — it is about a table row not
 * being handed a heading and three paragraphs.
 *
 * Parsing rather than a regex over tags, because a regex is what people
 * mistakenly reach for and then rely on. Block boundaries become spaces so the
 * end of one paragraph does not run into the start of the next.
 */
export function stripHtml(value: string): string {
  const doc = new DOMParser().parseFromString(value, "text/html");
  // A block ends a run of text. Without this, textContent glues the end of one
  // to the start of the next — a heading and its paragraph came out "Titlebold".
  doc.body
    .querySelectorAll("p, div, li, h1, h2, h3, h4, h5, h6, blockquote, pre, br, tr")
    .forEach((el) => el.after(doc.createTextNode(" ")));
  return (doc.body.textContent ?? "").replace(/\s+/g, " ").trim();
}
