/**
 * The API's types, re-exported from the generated schema.
 *
 * `api-schema.ts` is produced by `npm run gen:api` from the backend's own OpenAPI
 * document, so these names are the Pydantic schemas themselves rather than a
 * hand-written mirror of them that quietly falls behind. Add a field to a Read
 * schema in Python, regenerate, and every place the frontend is now wrong stops
 * compiling. (It was accurate when this switch was made — the point is that staying
 * accurate is no longer anybody's job.)
 *
 * The aliases exist because the app's names for things predate the API's and read
 * better in components: `Column`, not `ColumnRead`.
 *
 * **The `config` blobs are refined here, deliberately.** The backend types a
 * column's, a view's and a form's `config` as a plain `dict` — their shape depends
 * on the column type and is validated per type, which OpenAPI cannot express. So
 * the generated types say `Record<string, unknown>`, and the interfaces below say
 * what the UI actually relies on. That is a promise this file is making, not one
 * the backend is: keep them in step with the backend's column/view/form schemas by
 * hand.
 */
import type { components } from "./api-schema";

type Schemas = components["schemas"];

/**
 * Every field of a response, present.
 *
 * OpenAPI marks a field "not required" when its Pydantic model gives it a default
 * (`permissions_inherit`, `settings`, `is_deleted`…), and `openapi-typescript`
 * faithfully turns that into `field?: T`. But FastAPI serializes a response model
 * in full — defaults and nulls included — so those fields are always *there*; only
 * their value may be null. Without this, half the app would be `?? false`-ing
 * fields that cannot actually be absent.
 *
 * Note it is about *responses*. Request bodies genuinely may omit those fields, and
 * are typed from the API schema's own `…Create` / `…Update` models where it matters.
 */
type Response<T> = { [K in keyof T]-?: T[K] };

export type ResourceLink = Response<Schemas["ResourceLink"]>;
export type Workspace = Response<Schemas["WorkspaceRead"]>;
export type ListRead = Response<Schemas["ListRead"]>;
export type ListItem = Response<Schemas["ListItemRead"]>;
// A record in the bin: a ListItem plus when it was deleted.
export type DeletedListItem = Response<Schemas["DeletedListItemRead"]>;
// A record in the archive: a ListItem plus when it was archived. A separate axis
// from the bin — there is no retention window to count down here, so `archived_at`
// only ever says how long ago, never how long is left.
export type ArchivedListItem = Response<Schemas["ArchivedListItemRead"]>;
// Bin rows for workspaces and lists: the read shape plus when it was deleted. The
// backend exposes DeletedWorkspaceRead / DeletedListRead, so `npm run gen:api` will
// fold these into the generated schema; until it is re-run they mirror them here by
// extension, exactly as DeletedListItem does through the generated model.
export type DeletedWorkspace = Workspace & { deleted_at: string };
export type DeletedList = ListRead & { deleted_at: string };
/** One row of a list's config bin — a deleted column, view, form or metric. The
 *  same shape for all four, and deliberately narrower than their live schemas: it
 *  carries what identifies the thing and when it went, not its `config`. */
export type BinnedChild = Response<Schemas["BinnedChildRead"]>;
/** One row of the accounts/groups bin, narrow for the same reason. `detail` is the
 *  email of an account and the description of a group. */
export type BinnedPrincipal = Response<Schemas["BinnedPrincipalRead"]>;
export type Person = Response<Schemas["PersonRead"]>;
export type ApiUser = Response<Schemas["UserRead"]>;
export type ApiUserGroup = Response<Schemas["UserGroupRead"]>;
// Unlike the config blobs below, this one *is* published: the backend types it as a
// Pydantic model rather than a dict, so there is nothing to keep in step by hand.
export type UserSettings = Response<Schemas["UserSettings"]>;
export type UserSettingsUpdate = Schemas["UserSettingsUpdate"];
export type Grant = Response<Schemas["GrantRead"]>;

/** One account the batch importer created. `generated_password` is set only for the
 *  rows whose password the server minted, and it comes back exactly once — nothing
 *  stores it and there is no way to ask again. */
export type CreatedUser = Response<Schemas["CreatedUser"]>;
export type BatchUserError = Response<Schemas["BatchUserError"]>;
/** The result of a batch user import.
 *
 * The two arrays are re-spelled rather than left to `Response<>`, which is shallow:
 * it would make `created` present but leave its *elements* raw, so every consumer
 * would be back to `?? null`-ing fields that a response always carries. */
export type BatchUserSummary = Omit<
  Response<Schemas["BatchUserSummary"]>,
  "created" | "errors"
> & { created: CreatedUser[]; errors: BatchUserError[] };

export type ColumnType = Schemas["ColumnType"];
export type ConversionReport = Response<Schemas["ConversionReport"]>;
export type ConversionFailure = Response<Schemas["ConversionFailure"]>;
export type RecomputeReport = Response<Schemas["RecomputeReport"]>;
export type FormType = Schemas["FormType"];
export type ResourceType = Schemas["ResourceType"];
/** The backend calls it `Action`; in the UI it is always an action *on* something. */
export type PermissionAction = Schemas["Action"];

// ---- The config blobs: shapes the backend validates but does not publish ----

/** How a text column is edited. Unset means "single". "rich" is the one that
 *  changes what is *stored*: HTML rather than plain text (sanitised on write by
 *  the backend — see columns/richtext.py). */
export type TextMode = "single" | "multiline" | "rich";

export interface ColumnConfig {
  required?: boolean;
  default?: unknown;
  onupdate?: unknown;
  /** Text columns only. One line or several is an editing choice, not a storage
   *  one, so it lives here rather than in `type` — which also means it can be
   *  changed after the column exists, as `type` cannot. */
  text_mode?: TextMode;
  /** Choice options are plain labels; a cell stores the label itself. */
  options?: string[];
  /** Choice columns only. With this on the options are what the column *offers*:
   *  a cell may hold an answer of its own, stored as the typed string like any
   *  other label, so nothing downstream can tell the two apart. */
  allow_other?: boolean;
  target_list_uuid?: string;
  display_column_uuid?: string;
  /** Nested lookup: the sibling lookup whose value filters this one's options,
   *  and the target-list lookup column the filter matches on. Set together. */
  parent_column_uuid?: string | null;
  target_filter_column_uuid?: string | null;
  multiple?: boolean;
  allowed_schemes?: string[];
  label?: string;
  formula?: string;
  [key: string]: unknown;
}

export interface ViewSort {
  column_uuid: string;
  direction: "asc" | "desc";
}

export interface FilterCondition {
  column_uuid: string;
  operator: string;
  value?: unknown;
}

export interface FilterGroup {
  conjunction: "and" | "or";
  conditions: FilterCondition[];
}

export interface ViewConfig {
  filters?: FilterGroup;
  sorts?: ViewSort[];
  group_by?: { column_uuid: string } | null;
  hidden_columns?: string[];
  column_order?: string[];
  page_size?: number;
}

export interface FormField {
  column_uuid: string;
  included: boolean;
  enabled: boolean;
  order: number;
  label_override?: string | null;
}

/** One field a merged form borrows from the list its link column points at. */
export interface MergedField {
  column_uuid: string;
  included: boolean;
  enabled: boolean;
  order: number;
  label_override?: string | null;
}

export interface MergeConfig {
  /** The lookup column on *this* list through which the other one is reached. */
  link_column_uuid: string;
  /** Empty means no borrowed fields — never "all of them", unlike `FormConfig.fields`. */
  fields: MergedField[];
  /** False is 1:1: a linked record may be reached by at most one row of this list. */
  allow_shared: boolean;
}

export interface FormConfig {
  fields: FormField[];
  /** Absent on an ordinary single-list form, which is most of them. */
  merge?: MergeConfig | null;
}

/** The generated shape, with its untyped `config` replaced by the one the UI reads. */
export type Column = Omit<Response<Schemas["ColumnRead"]>, "config"> & {
  config: ColumnConfig;
};
export type View = Omit<Response<Schemas["ViewRead"]>, "config"> & {
  config: ViewConfig;
};
export type Form = Omit<Response<Schemas["FormRead"]>, "config"> & {
  config: FormConfig;
};

/** What restoring a column answers with: the column, plus the one consequence of
 *  its delete that the restore does *not* undo and can still be observed — the
 *  list being left without a primary column. Built on the refined `Column` so the
 *  `config` shape does not have to be stated twice. */
export type RestoredColumn = Column &
  Pick<Response<Schemas["RestoredColumnRead"]>, "list_has_no_primary_column">;

/** Metrics need no refinement, unlike the three above: a metric's config is one
 *  shape whatever the metric is, so the backend types it as a Pydantic model and
 *  the generated types already say what it holds. */
export type Metric = Response<Schemas["MetricRead"]>;
export type MetricConfig = Response<Schemas["MetricConfig"]>;
/** One card's answer. `value` and `error` are alternatives — a formula whose
 *  column was renamed reports why rather than reading zero. */
export type MetricValue = Response<Schemas["MetricValue"]>;
export type MetricValues = Response<Schemas["MetricValues"]>;
