import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";
import { InputNumber } from "primereact/inputnumber";
import { Dropdown } from "primereact/dropdown";
import { Checkbox } from "primereact/checkbox";
import { Calendar } from "primereact/calendar";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { NameFields } from "../../components/NameFields";
import { isValidName } from "../../lib/slug";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { isTemporal, parseTemporal, serializeTemporal } from "../../lib/temporal";
import { useToast } from "../../lib/toast";
import type { Column, ColumnType, ListRead, Workspace } from "../../lib/types";

// The dynamic default/on-update token a type auto-fills with, and its human label.
// Temporal columns stamp the current date/time; a user column stamps the actor.
function dynamicToken(type: ColumnType): string | null {
  if (type === "date") return "today";
  if (type === "time" || type === "datetime") return "now";
  if (type === "user") return "current_user";
  return null;
}
function dynamicLabel(type: ColumnType): string {
  return type === "date" ? "Today" : type === "user" ? "Current user" : "Now";
}
// Sentinel for the temporal "Default value" dropdown meaning "a fixed value I pick".
const SPECIFIC = "__specific__";
// Sentinel for the lookup "Workspace" dropdown: lists shared with the user from
// other people's workspaces, which don't belong to a workspace the user owns.
const SHARED = "__shared__";

interface Props {
  listUuid: string;
  workspaceUuid: string | null;
  // null => create a new column; otherwise edit this one.
  column: Column | null;
  visible: boolean;
  onHide: () => void;
}

/** Add or modify a column. The two are one dialog because they take the same
 *  settings — with one exception the API enforces: a column's **type is immutable**
 *  once records hold values shaped by it, so it is shown but locked when editing. */
export function ColumnDialog({
  listUuid,
  workspaceUuid,
  column,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const editing = column !== null;

  const [displayName, setDisplayName] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<ColumnType>("text");
  const [required, setRequired] = useState(false);
  const [unique, setUnique] = useState(false);
  const [maxLength, setMaxLength] = useState<number | null>(null);
  const [min, setMin] = useState<number | null>(null);
  const [max, setMax] = useState<number | null>(null);
  const [integerOnly, setIntegerOnly] = useState(false);
  const [choices, setChoices] = useState("");
  const [linkText, setLinkText] = useState("");
  // Which workspace's lists the target dropdown is drawn from. A lookup can point
  // at any list the user can view — their own across workspaces, or one shared
  // with them (the SHARED bucket). Defaults to the current list's workspace.
  const [targetWorkspace, setTargetWorkspace] = useState<string | null>(null);
  const [targetListUuid, setTargetListUuid] = useState<string | null>(null);
  const [displayColumnUuid, setDisplayColumnUuid] = useState<string | null>(null);
  // Nested lookup: the sibling lookup this one nests under, and the target-list
  // lookup column its filter matches on. Set and cleared together.
  const [parentColumnUuid, setParentColumnUuid] = useState<string | null>(null);
  const [targetFilterColumnUuid, setTargetFilterColumnUuid] = useState<
    string | null
  >(null);
  const [multiple, setMultiple] = useState(false);
  const [formula, setFormula] = useState("");
  // Default value — one piece of state per family of column type; only the one
  // matching `type` is read when the config is built.
  const [defaultText, setDefaultText] = useState(""); // text / long_text / link
  const [defaultNumber, setDefaultNumber] = useState<number | null>(null);
  const [defaultChecked, setDefaultChecked] = useState(false);
  const [defaultChoice, setDefaultChoice] = useState<string | null>(null);
  const [defaultTemporal, setDefaultTemporal] = useState(""); // "" | token | SPECIFIC
  const [defaultDate, setDefaultDate] = useState<Date | null>(null); // when SPECIFIC
  const [defaultUser, setDefaultUser] = useState(""); // "" | "current_user"
  const [onUpdate, setOnUpdate] = useState(""); // "" | token (temporal / user only)

  // Clear every default/on-update control. Called when starting a new column and
  // when the type changes, so a value entered for one type can't leak into another.
  function resetDefaults() {
    setDefaultText("");
    setDefaultNumber(null);
    setDefaultChecked(false);
    setDefaultChoice(null);
    setDefaultTemporal("");
    setDefaultDate(null);
    setDefaultUser("");
    setOnUpdate("");
  }

  // Seed the form from the column being edited (or clear it for a new one) each
  // time the dialog opens.
  useEffect(() => {
    if (!visible) return;
    if (!column) {
      setDisplayName("");
      setName("");
      setType("text");
      setRequired(false);
      setUnique(false);
      setMaxLength(null);
      setMin(null);
      setMax(null);
      setIntegerOnly(false);
      setChoices("");
      setLinkText("");
      setTargetWorkspace(workspaceUuid);
      setTargetListUuid(null);
      setDisplayColumnUuid(null);
      setParentColumnUuid(null);
      setTargetFilterColumnUuid(null);
      setMultiple(false);
      setFormula("");
      resetDefaults();
      return;
    }
    const config = column.config ?? {};
    setDisplayName(column.display_name);
    setName(column.name);
    setType(column.type);
    setRequired(!!config.required);
    setUnique(!!config.unique);
    setMaxLength((config.max_length as number) ?? null);
    setMin((config.min as number) ?? null);
    setMax((config.max as number) ?? null);
    setIntegerOnly(!!config.integer_only);
    setChoices(((config.options as string[]) ?? []).join(";"));
    setLinkText((config.label as string) ?? "");
    // Left null so the seeding effect can resolve which workspace the stored
    // target lives in (the config only records the list, not its workspace).
    setTargetWorkspace(null);
    setTargetListUuid((config.target_list_uuid as string) ?? null);
    setDisplayColumnUuid((config.display_column_uuid as string) ?? null);
    setParentColumnUuid((config.parent_column_uuid as string) ?? null);
    setTargetFilterColumnUuid(
      (config.target_filter_column_uuid as string) ?? null,
    );
    setMultiple(!!config.multiple);
    setFormula((config.formula as string) ?? "");

    // Seed the default/on-update controls from the stored config.
    resetDefaults();
    const def = config.default;
    if (typeof def === "string") setDefaultText(def);
    if (typeof def === "number") setDefaultNumber(def);
    setDefaultChecked(def === true);
    // A choice default is stored as the option itself.
    if (typeof def === "string") setDefaultChoice(def);
    if (isTemporal(column.type)) {
      const token = dynamicToken(column.type);
      if (def == null) setDefaultTemporal("");
      else if (def === token) setDefaultTemporal(token);
      else {
        setDefaultTemporal(SPECIFIC);
        setDefaultDate(parseTemporal(column.type, def));
      }
    }
    if (column.type === "user" && def === "current_user")
      setDefaultUser("current_user");
    if (typeof config.onupdate === "string") setOnUpdate(config.onupdate);
  }, [visible, column, workspaceUuid]);

  // The workspaces the user owns, plus a "Shared with me" bucket — the scopes a
  // lookup target may be drawn from.
  const myWorkspaces = useQuery({
    queryKey: ["workspaces-all"],
    queryFn: () => api.get<Workspace[]>(`/workspaces/?limit=1000`),
    enabled: type === "lookup",
  });

  // On edit, the stored config names only the target list, not its workspace, so
  // resolve which bucket it lives in and open the selector there. Fetching the
  // list tells us its workspace; if that isn't one the user owns (or the fetch is
  // forbidden), it must have been shared with them.
  const storedTarget = column?.config.target_list_uuid as string | undefined;
  const needSeed = type === "lookup" && !!storedTarget && targetWorkspace === null;
  const seedMeta = useQuery({
    queryKey: ["list-meta", storedTarget],
    queryFn: () => api.get<ListRead>(`/lists/${storedTarget}`),
    enabled: needSeed,
    retry: false,
  });
  useEffect(() => {
    if (!needSeed || seedMeta.isLoading || myWorkspaces.isLoading) return;
    const wsUuid = seedMeta.data?.workspace?.uuid ?? null;
    const mine = (myWorkspaces.data ?? []).some((w) => w.uuid === wsUuid);
    setTargetWorkspace(wsUuid && mine ? wsUuid : SHARED);
  }, [
    needSeed,
    seedMeta.isLoading,
    seedMeta.data,
    myWorkspaces.isLoading,
    myWorkspaces.data,
  ]);

  // Candidate target lists in the chosen workspace (self included), or every list
  // shared with the user when the SHARED bucket is chosen.
  const lists = useQuery({
    queryKey: ["lookup-target-lists", targetWorkspace],
    queryFn: () =>
      api.get<ListRead[]>(
        targetWorkspace === SHARED
          ? `/lists/?shared=true&limit=1000`
          : `/lists/?workspace_uuid=${targetWorkspace}&limit=1000`,
      ),
    enabled: type === "lookup" && !!targetWorkspace,
  });
  // Keep the currently-selected target visible even before its workspace's lists
  // load (the moment the dialog opens on edit): show it, labelled from the seed
  // fetch, until the full list arrives.
  const listOptions = useMemo(() => {
    const opts = (lists.data ?? []).map((l) => ({
      label: l.display_name,
      value: l.uuid,
    }));
    if (targetListUuid && !opts.some((o) => o.value === targetListUuid)) {
      opts.unshift({
        label: seedMeta.data?.display_name ?? "…",
        value: targetListUuid,
      });
    }
    return opts;
  }, [lists.data, targetListUuid, seedMeta.data]);
  // Columns of the chosen target list, offered as the label to show per row.
  const targetColumns = useQuery({
    queryKey: ["columns", targetListUuid],
    queryFn: () => api.get<Column[]>(`/lists/${targetListUuid}/columns/`),
    enabled: type === "lookup" && !!targetListUuid,
  });
  // This list's own columns — for a formula, the columns it may reference; for a
  // lookup, the sibling lookups it could nest under.
  const ownColumns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
    enabled: type === "calculated" || type === "lookup",
  });
  const referenceableNames = (ownColumns.data ?? [])
    .filter(
      (c) =>
        (c.type === "number" || c.type === "text" || c.type === "long_text") &&
        c.uuid !== column?.uuid,
    )
    .map((c) => c.name);

  // Nested lookup candidates. A sibling lookup qualifies as a parent when the
  // target list has at least one lookup column pointing back at the sibling's
  // target — that column is what the filter matches on ("Cities.Country" for a
  // "Country" parent). Both dropdowns are derived, so an impossible pairing is
  // simply never offered.
  const targetLookups = (targetColumns.data ?? []).filter(
    (c) => c.type === "lookup" && !!c.config.target_list_uuid,
  );
  const parentOptions = (ownColumns.data ?? []).filter(
    (c) =>
      c.type === "lookup" &&
      c.uuid !== column?.uuid &&
      !!c.config.target_list_uuid &&
      targetLookups.some(
        (t) => t.config.target_list_uuid === c.config.target_list_uuid,
      ),
  );
  const parentColumn =
    parentOptions.find((c) => c.uuid === parentColumnUuid) ?? null;
  const matchOptions = parentColumn
    ? targetLookups.filter(
        (t) => t.config.target_list_uuid === parentColumn.config.target_list_uuid,
      )
    : [];

  // A multi-value column holds an array, and the uniqueness check compares whole
  // stored values — so it would ask whether two records picked the *same set*,
  // which is not what anyone means by a unique column. Hide it, and clear it.
  const multiValued =
    (type === "user" || type === "group" || type === "lookup") && multiple;
  // A calculated column is derived on read and stores no value, so required and
  // unique have nothing to act on.
  const isCalculated = type === "calculated";

  // Types that offer an auto-stamp on write (a "last modified" date, "edited by" user).
  const supportsOnUpdate = isTemporal(type) || type === "user";
  // The default is hidden once an on-update stamp is set: the stamp always
  // overwrites, so a default could never take effect, and showing both misleads.
  const showDefault =
    !isCalculated &&
    type !== "lookup" &&
    type !== "group" &&
    !(supportsOnUpdate && onUpdate !== "");
  // Options for the choice "default option" dropdown, from the shorthand as typed.
  const choiceLabels = choices
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);

  const save = useMutation({
    mutationFn: () => {
      // `unique` is always sent, never omitted: a config PATCH merges onto the
      // stored one, so leaving it out would preserve a `true` this dialog is
      // currently hiding — a constraint still enforced but no longer visible.
      const config: Record<string, unknown> = isCalculated
        ? { formula: formula.trim() }
        : {
            required,
            unique: multiValued ? false : unique,
          };
      if (type === "text" || type === "long_text") {
        config.max_length = maxLength;
      }
      if (type === "number") {
        config.min = min;
        config.max = max;
        config.integer_only = integerOnly;
      }
      if (type === "choice") config.choices = choices;
      if (type === "link") config.label = linkText.trim() || null;
      if (type === "user" || type === "group") config.multiple = multiple;
      if (type === "lookup") {
        config.target_list_uuid = targetListUuid;
        config.display_column_uuid = displayColumnUuid;
        config.multiple = multiple;
        // Always sent, like `unique`: a config PATCH merges, so un-nesting
        // requires explicit nulls rather than omitted keys.
        config.parent_column_uuid = parentColumnUuid;
        config.target_filter_column_uuid = parentColumnUuid
          ? targetFilterColumnUuid
          : null;
      }

      // Default value — sent for every type that supports one (not lookup, group,
      // or calculated). Always sent, even when empty, so clearing it on edit takes
      // effect: a config PATCH merges, so an omitted key would keep the old default.
      if (!isCalculated && type !== "lookup" && type !== "group") {
        let defaultValue: unknown = null;
        if (type === "text" || type === "long_text" || type === "link")
          defaultValue = defaultText.trim() || null;
        else if (type === "number") defaultValue = defaultNumber;
        else if (type === "checkbox") defaultValue = defaultChecked ? true : null;
        else if (type === "choice") defaultValue = defaultChoice || null;
        else if (isTemporal(type))
          defaultValue =
            defaultTemporal === SPECIFIC
              ? defaultDate
                ? serializeTemporal(type, defaultDate)
                : null
              : defaultTemporal || null;
        else if (type === "user") defaultValue = defaultUser || null;
        config.default = defaultValue;
      }
      // On-update auto-stamp — temporal and user columns only.
      if (isTemporal(type) || type === "user") {
        config.onupdate = onUpdate || null;
      }

      if (editing) {
        return api.patch<Column>(`/lists/${listUuid}/columns/${column.uuid}`, {
          name,
          display_name: displayName,
          config,
        });
      }
      return api.post<Column>(`/lists/${listUuid}/columns/`, {
        name,
        display_name: displayName,
        type,
        config,
      });
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      // Turning `multiple` off rewrites records, and the labels a lookup/user cell
      // renders are keyed on the values it holds.
      queryClient.invalidateQueries({ queryKey: ["lookup-labels"] });
      queryClient.invalidateQueries({ queryKey: ["principal-labels"] });
      toast.success(editing ? "Column updated" : "Column added", saved.display_name);
      onHide();
    },
  });

  // Going from several values to one is the only edit here that loses data, so it
  // asks first. The records are not rewritten on the spot (on a large list that
  // would be a multi-minute lock inside this request) — the extra values are hidden
  // immediately and cleared out later, which is exactly what the message promises.
  function onSave() {
    const losingValues = editing && !!column.config.multiple && !multiple;
    if (!losingValues) {
      save.mutate();
      return;
    }
    confirmDialog({
      header: "Keep only the first value?",
      icon: "pi pi-exclamation-triangle",
      message: `“${column.display_name}” currently allows several values. Records holding more than one will show only the first from now on, and the extra values are removed for good the next time the records are edited or cleaned up. Switching back to multiple before then restores them.`,
      acceptLabel: "Keep first value",
      acceptClassName: "p-button-danger",
      rejectLabel: "Cancel",
      accept: () => save.mutate(),
    });
  }

  return (
    <AppDialog
      header={editing ? `Edit “${column.display_name}”` : "Add column"}
      visible={visible}
      onHide={onHide}
      style={{ width: 420 }}
    >
      <div className="flex flex-col gap-4">
        {save.error instanceof ApiError && (
          <Message severity="error" text={save.error.message} />
        )}
        <NameFields
          key={`${column?.uuid ?? "new"}:${visible}`}
          idPrefix="col"
          value={{ displayName, name }}
          onChange={(next) => {
            setDisplayName(next.displayName);
            setName(next.name);
          }}
          linked={!editing}
          autoFocus
        />
        <div className="flex flex-col gap-2">
          <label htmlFor="col-type">Type</label>
          <Dropdown
            inputId="col-type"
            value={type}
            options={COLUMN_TYPE_OPTIONS}
            onChange={(e) => {
              setType(e.value as ColumnType);
              // The default/on-update controls are type-specific.
              resetDefaults();
            }}
            disabled={editing}
          />
          {editing && (
            <small style={{ color: "var(--text-muted)" }}>
              A column's type cannot change: existing records already hold values in
              its shape.
            </small>
          )}
        </div>

        {(type === "text" || type === "long_text") && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-maxlen">Max length (optional)</label>
            <InputNumber
              inputId="col-maxlen"
              value={maxLength}
              onValueChange={(e) => setMaxLength(e.value ?? null)}
              min={1}
              useGrouping={false}
            />
          </div>
        )}

        {type === "number" && (
          <>
            <div className="flex gap-3">
              <div className="flex flex-col gap-2 flex-1">
                <label htmlFor="col-min">Min</label>
                <InputNumber
                  inputId="col-min"
                  value={min}
                  onValueChange={(e) => setMin(e.value ?? null)}
                  useGrouping={false}
                />
              </div>
              <div className="flex flex-col gap-2 flex-1">
                <label htmlFor="col-max">Max</label>
                <InputNumber
                  inputId="col-max"
                  value={max}
                  onValueChange={(e) => setMax(e.value ?? null)}
                  useGrouping={false}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                inputId="col-int"
                checked={integerOnly}
                onChange={(e) => setIntegerOnly(!!e.checked)}
              />
              <label htmlFor="col-int">Integers only</label>
            </div>
          </>
        )}

        {type === "choice" && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-choices">Choices (separated by ";")</label>
            <InputText
              id="col-choices"
              value={choices}
              onChange={(e) => setChoices(e.target.value)}
              placeholder="Todo;Doing;Done"
            />
            {editing && (
              <small style={{ color: "var(--text-muted)" }}>
                Renaming an option keeps records pointing at it; removing one leaves
                records holding a value the column no longer offers.
              </small>
            )}
          </div>
        )}

        {type === "link" && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-linktext">Link text (optional)</label>
            <InputText
              id="col-linktext"
              value={linkText}
              onChange={(e) => setLinkText(e.target.value)}
              placeholder="Shown instead of the URL"
            />
          </div>
        )}

        {(type === "user" || type === "group") && (
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="col-multi-principal"
              checked={multiple}
              onChange={(e) => setMultiple(!!e.checked)}
            />
            <label htmlFor="col-multi-principal">
              Allow selecting multiple {type === "user" ? "users" : "groups"}
            </label>
          </div>
        )}

        {type === "lookup" && (
          <>
            <div className="flex flex-col gap-2">
              <label htmlFor="col-target-ws">Workspace</label>
              <Dropdown
                inputId="col-target-ws"
                value={targetWorkspace}
                options={[
                  ...(myWorkspaces.data ?? []).map((w) => ({
                    label: w.display_name,
                    value: w.uuid,
                  })),
                  { label: "Shared with me", value: SHARED },
                ]}
                onChange={(e) => {
                  setTargetWorkspace(e.value);
                  // The list, its display column and any nesting all belong to
                  // the workspace being left; clear them so nothing dangles.
                  setTargetListUuid(null);
                  setDisplayColumnUuid(null);
                  setParentColumnUuid(null);
                  setTargetFilterColumnUuid(null);
                }}
                placeholder={
                  myWorkspaces.isLoading ? "Loading…" : "Select a workspace…"
                }
                filter
              />
              <small style={{ color: "var(--text-muted)" }}>
                Point at a list in any of your workspaces, or one shared with you.
              </small>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor="col-target">Target list</label>
              <Dropdown
                inputId="col-target"
                value={targetListUuid}
                options={listOptions}
                onChange={(e) => {
                  setTargetListUuid(e.value);
                  setDisplayColumnUuid(null); // columns differ per target list
                  // A nesting pairing is specific to the old target list.
                  setParentColumnUuid(null);
                  setTargetFilterColumnUuid(null);
                }}
                placeholder={lists.isLoading ? "Loading…" : "Select a list…"}
                filter
              />
            </div>
            {targetListUuid && (
              <div className="flex flex-col gap-2">
                <label htmlFor="col-display">Column to show</label>
                <Dropdown
                  inputId="col-display"
                  value={displayColumnUuid}
                  options={(targetColumns.data ?? []).map((c) => ({
                    label: c.display_name,
                    value: c.uuid,
                  }))}
                  onChange={(e) => setDisplayColumnUuid(e.value)}
                  placeholder={
                    targetColumns.isLoading ? "Loading…" : "Primary column"
                  }
                  showClear
                  filter
                />
              </div>
            )}
            {targetListUuid && (parentOptions.length > 0 || parentColumnUuid) && (
              <div className="flex flex-col gap-2">
                <label htmlFor="col-nest-parent">Nest under (optional)</label>
                <Dropdown
                  inputId="col-nest-parent"
                  value={parentColumnUuid}
                  options={parentOptions.map((c) => ({
                    label: c.display_name,
                    value: c.uuid,
                  }))}
                  onChange={(e) => {
                    const next = (e.value as string) ?? null;
                    setParentColumnUuid(next);
                    // The match column depends on the parent; re-derive it, and
                    // save a click when only one column qualifies.
                    const candidates = next
                      ? targetLookups.filter((t) => {
                          const parent = parentOptions.find(
                            (c) => c.uuid === next,
                          );
                          return (
                            t.config.target_list_uuid ===
                            parent?.config.target_list_uuid
                          );
                        })
                      : [];
                    setTargetFilterColumnUuid(
                      candidates.length === 1 ? candidates[0].uuid : null,
                    );
                  }}
                  placeholder="Not nested"
                  showClear
                />
                <small style={{ color: "var(--text-muted)" }}>
                  What's selected there decides which rows this column offers —
                  pick a country first, get only its cities.
                </small>
                {parentColumnUuid && (
                  <>
                    <label htmlFor="col-nest-match">Match on</label>
                    <Dropdown
                      inputId="col-nest-match"
                      value={targetFilterColumnUuid}
                      options={matchOptions.map((c) => ({
                        label: c.display_name,
                        value: c.uuid,
                      }))}
                      onChange={(e) =>
                        setTargetFilterColumnUuid((e.value as string) ?? null)
                      }
                      placeholder="Select a column…"
                    />
                    <small style={{ color: "var(--text-muted)" }}>
                      The target list's column that links each row to a{" "}
                      {parentColumn?.display_name ?? "parent"} selection.
                    </small>
                  </>
                )}
              </div>
            )}
            <div className="flex items-center gap-2">
              <Checkbox
                inputId="col-multiple"
                checked={multiple}
                onChange={(e) => setMultiple(!!e.checked)}
              />
              <label htmlFor="col-multiple">Allow linking multiple rows</label>
            </div>
          </>
        )}

        {isCalculated && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-formula">Formula</label>
            <InputTextarea
              id="col-formula"
              value={formula}
              onChange={(e) => setFormula(e.target.value)}
              rows={2}
              placeholder="qty * price"
              style={{ fontFamily: "monospace" }}
            />
            <small style={{ color: "var(--text-muted)" }}>
              Computed from other columns each time a record is read; it has no
              stored value of its own. Numbers: <code>+ - * /</code>, parentheses,{" "}
              <code>min</code>, <code>max</code>, <code>round</code>,{" "}
              <code>abs</code>. Text: <code>CONCAT</code>, <code>UPPER</code>,{" "}
              <code>LOWER</code>, <code>TRIM</code>, <code>LEN</code>,{" "}
              <code>LEFT</code>, <code>RIGHT</code>, <code>REPLACE</code>. And{" "}
              <code>IF(cond, a, b)</code>.
              {referenceableNames.length > 0 ? (
                <>
                  {" "}
                  Columns you can reference: {referenceableNames.join(", ")}.
                </>
              ) : (
                <>
                  {" "}
                  Add a number or text column first — a formula references those.
                </>
              )}
            </small>
          </div>
        )}

        {showDefault && (
          <div className="flex flex-col gap-2">
            {type !== "checkbox" && (
              <label htmlFor="col-default">Default value</label>
            )}
            {(type === "text" || type === "long_text" || type === "link") && (
              <InputText
                id="col-default"
                value={defaultText}
                onChange={(e) => setDefaultText(e.target.value)}
                placeholder={
                  type === "link" ? "https://example.com" : "Used when left blank"
                }
              />
            )}
            {type === "number" && (
              <InputNumber
                inputId="col-default"
                value={defaultNumber}
                onValueChange={(e) => setDefaultNumber(e.value ?? null)}
                useGrouping={false}
              />
            )}
            {type === "checkbox" && (
              <div className="flex items-center gap-2">
                <Checkbox
                  inputId="col-default"
                  checked={defaultChecked}
                  onChange={(e) => setDefaultChecked(!!e.checked)}
                />
                <label htmlFor="col-default">New records start checked</label>
              </div>
            )}
            {type === "choice" && (
              <Dropdown
                inputId="col-default"
                value={defaultChoice}
                options={choiceLabels.map((l) => ({ label: l, value: l }))}
                onChange={(e) => setDefaultChoice(e.value ?? null)}
                placeholder="No default"
                showClear
              />
            )}
            {isTemporal(type) && (
              <>
                <Dropdown
                  inputId="col-default"
                  value={defaultTemporal}
                  options={[
                    { label: "No default", value: "" },
                    {
                      label: dynamicLabel(type),
                      value: dynamicToken(type) as string,
                    },
                    { label: "Specific value…", value: SPECIFIC },
                  ]}
                  onChange={(e) => setDefaultTemporal(e.value)}
                />
                {defaultTemporal === SPECIFIC && (
                  <Calendar
                    value={defaultDate}
                    onChange={(e) => setDefaultDate((e.value as Date) ?? null)}
                    dateFormat="yy-mm-dd"
                    timeOnly={type === "time"}
                    showTime={type === "datetime"}
                    showButtonBar={type === "date"}
                  />
                )}
              </>
            )}
            {type === "user" && (
              <Dropdown
                inputId="col-default"
                value={defaultUser}
                options={[
                  { label: "No default", value: "" },
                  { label: "Current user", value: "current_user" },
                ]}
                onChange={(e) => setDefaultUser(e.value)}
              />
            )}
          </div>
        )}

        {supportsOnUpdate && (
          <div className="flex flex-col gap-2">
            <label htmlFor="col-onupdate">On update</label>
            <Dropdown
              inputId="col-onupdate"
              value={onUpdate}
              options={[
                { label: "Off", value: "" },
                { label: dynamicLabel(type), value: dynamicToken(type) as string },
              ]}
              onChange={(e) => setOnUpdate(e.value)}
            />
            <small style={{ color: "var(--text-muted)" }}>
              Filled in automatically when a record is created and re-stamped on
              every edit; it isn't set by hand.
            </small>
          </div>
        )}

        {!isCalculated && (
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="col-required"
              checked={required}
              onChange={(e) => setRequired(!!e.checked)}
            />
            <label htmlFor="col-required">Required</label>
          </div>
        )}

        {!isCalculated && !multiValued && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Checkbox
                inputId="col-unique"
                checked={unique}
                onChange={(e) => setUnique(!!e.checked)}
              />
              <label htmlFor="col-unique">Unique</label>
            </div>
            {editing && !column.config.unique && unique && (
              <small style={{ color: "var(--text-muted)" }}>
                Existing duplicates are left alone — this is checked when a record is
                saved, so they surface the next time one of them is edited.
              </small>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={editing ? "Save" : "Add"}
            onClick={onSave}
            loading={save.isPending}
            disabled={
              !displayName.trim() ||
              !isValidName(name) ||
              (type === "lookup" && !targetListUuid) ||
              // A half-configured nesting: the pairing is set together or not at all.
              (type === "lookup" && !!parentColumnUuid && !targetFilterColumnUuid) ||
              (isCalculated && !formula.trim())
            }
          />
        </div>
      </div>
    </AppDialog>
  );
}
