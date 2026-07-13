// The backend's NameSlug (src/core/schemas.py). Kept in step by hand: the price of
// checking here is that a typo is caught before a round trip, not that the server
// stops checking.
const NAME = /^[a-z][a-z0-9_]*$/;

export function isValidName(name: string): boolean {
  return NAME.test(name) && name.length <= 63;
}

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
