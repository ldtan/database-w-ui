// Hand-written mirrors of the backend Read schemas. Run `npm run gen:api`
// (with the backend running) to generate exact types into api-schema.ts.

export interface ResourceLink {
  uuid: string;
}

export interface Workspace {
  uuid: string;
  name: string;
  display_name: string;
  description?: string | null;
  settings: Record<string, unknown>;
}

export interface ListRead {
  uuid: string;
  name: string;
  display_name: string;
  description?: string | null;
  settings: Record<string, unknown>;
  workspace: ResourceLink;
  primary_column?: ResourceLink | null;
  permissions_inherit: boolean;
}

export type ColumnType =
  | "text"
  | "long_text"
  | "number"
  | "checkbox"
  | "choice"
  | "lookup"
  | "link"
  | "date"
  | "time"
  | "datetime";

export interface ChoiceOption {
  id: string;
  label: string;
  color?: string | null;
}

export interface ColumnConfig {
  required?: boolean;
  options?: ChoiceOption[];
  target_list_uuid?: string;
  display_column_uuid?: string;
  multiple?: boolean;
  allowed_schemes?: string[];
  label?: string;
  [key: string]: unknown;
}

export interface Column {
  uuid: string;
  name: string;
  display_name: string;
  type: ColumnType;
  position: number;
  config: ColumnConfig;
}

export interface ListItem {
  uuid: string;
  data: Record<string, unknown>;
  permissions_inherit: boolean;
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

export interface View {
  uuid: string;
  name: string;
  display_name: string;
  position: number;
  config: ViewConfig;
  permissions_inherit: boolean;
}

export type FormType = "create" | "update" | "custom";

export interface FormField {
  column_uuid: string;
  included: boolean;
  enabled: boolean;
  order: number;
  label_override?: string | null;
}

export interface FormConfig {
  fields: FormField[];
}

export interface Form {
  uuid: string;
  name: string;
  display_name: string;
  type: FormType;
  is_default: boolean;
  config: FormConfig;
  permissions_inherit: boolean;
}

// ---- Permissions ----

export type ResourceType = "workspace" | "list" | "view" | "form" | "item";

export type PermissionAction =
  | "view"
  | "create"
  | "edit"
  | "delete"
  | "configure"
  | "share"
  | "manage";

export interface Grant {
  uuid: string;
  resource_type: ResourceType;
  resource_uuid: string;
  user_uuid?: string | null;
  user_username?: string | null;
  group_uuid?: string | null;
  group_name?: string | null;
  actions: PermissionAction[];
}

export interface ApiUser {
  uuid: string;
  username: string;
  email: string;
  is_active: boolean;
  // Administers accounts and groups — not a grant of access to other people's data.
  is_superuser: boolean;
  groups: ResourceLink[];
}

// A user's personal details. Optional — an account can exist without one.
export interface Person {
  uuid: string;
  first_name: string;
  middle_name?: string | null;
  last_name: string;
  birth_date?: string | null; // ISO date, no time component
  user: ResourceLink;
}

export interface ApiUserGroup {
  uuid: string;
  name: string;
  description?: string | null;
  users: ResourceLink[];
}
