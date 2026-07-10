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
}

export type ColumnType =
  | "text"
  | "long_text"
  | "number"
  | "checkbox"
  | "choice"
  | "lookup"
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
}

export interface ViewSort {
  column_uuid: string;
  direction: "asc" | "desc";
}

export interface ViewConfig {
  filters?: { conjunction: "and" | "or"; conditions: unknown[] };
  sorts?: ViewSort[];
  group_by?: { column_uuid: string } | null;
  page_size?: number;
}

export interface View {
  uuid: string;
  name: string;
  display_name: string;
  position: number;
  config: ViewConfig;
}
