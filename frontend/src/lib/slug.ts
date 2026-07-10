// Turn a free-text display name into a backend-valid `name` slug
// (^[a-z][a-z0-9_]*$). Used as a convenience when creating resources.
export function slugify(input: string): string {
  const slug = input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!slug) return "item";
  return /^[a-z]/.test(slug) ? slug : `x_${slug}`;
}
