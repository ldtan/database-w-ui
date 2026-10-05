/** Server limits the SPA has to know in advance.
 *
 * A static build cannot read the backend's settings, so anything the UI must
 * *respect* rather than merely react to is mirrored here by hand. One module
 * rather than a constant per feature: the grid and the bin dialog both chunk
 * against the same number, and two copies drifting apart would be two different
 * answers to "how many is too many".
 */

/** Uuids one bulk record endpoint accepts per call — `bulk-delete`,
 *  `bulk-archive`, `bulk-unarchive`. A larger selection is sent as several calls.
 *
 *  The server's limit is `BULK_DELETE_MAX` in its config, defaulting to the same
 *  500, and it is published through the OpenAPI schema. The server's may be raised
 *  freely; lowering it below this number would produce a validation error naming a
 *  limit the person never saw, so this has to come down first.
 */
export const BULK_MAX = 500;
