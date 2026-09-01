import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";
import { InputNumber } from "primereact/inputnumber";
import { Dropdown } from "primereact/dropdown";
import { MultiSelect } from "primereact/multiselect";
import { Calendar } from "primereact/calendar";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { SelectButton } from "primereact/selectbutton";
import { confirmDialog } from "primereact/confirmdialog";

import { api } from "../../lib/api";
import {
  CheckboxField,
  Field,
  FieldRow,
  FormSection,
} from "../../components/Field";
import { NameFields } from "../../components/NameFields";
import { isValidName } from "../../lib/slug";
import {
  COLUMN_TYPE_OPTIONS,
  conversionTargets,
  duplicateTargets,
} from "../../lib/columnTypes";
import {
  countryChoices,
  phoneExample,
  type PhoneFormat,
} from "../../lib/phone";
import type { CountryCode } from "libphonenumber-js/max";
import { RATING_MAX } from "../../lib/rating";
import { isTemporal, parseTemporal, serializeTemporal } from "../../lib/temporal";
import { useToast } from "../../lib/toast";
import type {
  Column,
  ColumnType,
  ConversionReport,
  FilterCondition,
  ListRead,
  TextMode,
  Workspace,
} from "../../lib/types";
import { FilterConditionsEditor } from "../views/FilterConditionsEditor";
import { cleanConditions } from "../views/operators";
import { hasAdvancedConfig } from "./columnAdvanced";
import { ErrorMessage } from "../../components/ErrorMessage";

// The first two are what used to be the `text` and `long_text` column types.
// "Rich text" is the one that changes what is stored — HTML, not plain text.
const TEXT_MODE_OPTIONS: { label: string; value: TextMode }[] = [
  { label: "Single line", value: "single" },
  { label: "Multiple lines", value: "multiline" },
  { label: "Rich text", value: "rich" },
];

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
const RATING_ICON_OPTIONS = [
  { label: "Stars", value: "star" },
  { label: "Hearts", value: "heart" },
  { label: "A number", value: "number" },
];

// A cell always holds seconds; this is only how they are written. Kept in step
// with `_DURATION_FORMATS` on the server and `DurationFormat` in lib/duration.ts.
const DURATION_FORMAT_OPTIONS = [
  { label: "1:30 (hours:minutes)", value: "h:mm" },
  { label: "1:30:00 (hours:minutes:seconds)", value: "h:mm:ss" },
  { label: "1:30 (minutes:seconds)", value: "m:ss" },
  { label: "1.50 (decimal hours)", value: "decimal_hours" },
];

// A cell always holds E.164; this is only how it is drawn. Kept in step with
// `_PHONE_FORMATS` on the server and `PhoneFormat` in lib/phone.ts.
//
// Where the column names a country the examples are drawn from *it*, because a
// fixed `+63 917 123 4567` describes the choice correctly on a Philippine column
// and misdescribes it on every other — and this dropdown is the one place somebody
// compares the three forms side by side.
//
// Where it names none there is no country to speak for, so the options describe
// the *shape* instead. Falling back to an example from some default country would
// be the same guess `initialRegion` refuses to make, and worse here: `local` has
// no example without a country at all, so two of the three options would render
// identically and one of them would be a lie.
const PHONE_FORMATS: { label: string; shape: string; value: PhoneFormat }[] = [
  { label: "international", shape: "International — country code and spaces", value: "international" },
  { label: "local", shape: "Local — no country code", value: "national" },
  { label: "E.164", shape: "E.164 — country code, no spaces", value: "e164" },
];

function phoneFormatOptions(region: CountryCode | undefined) {
  return PHONE_FORMATS.map(({ label, shape, value }) => ({
    label: region ? `${phoneExample(region, value)} (${label})` : shape,
    value,
  }));
}

// Which map site a location cell's external link opens. Kept in step with
// `_MAP_LINKS` on the server and `mapHref` in lib/location.ts.
const MAP_LINK_OPTIONS = [
  { label: "OpenStreetMap", value: "osm" },
  { label: "Google Maps", value: "google" },
  { label: "Apple Maps", value: "apple" },
];

// Sentinel for the temporal "Default value" dropdown meaning "a fixed value I pick".
const SPECIFIC = "__specific__";
// Sentinel for the lookup "Workspace" dropdown: lists shared with the user from
// other people's workspaces, which don't belong to a workspace the user owns.
const SHARED = "__shared__";

// What a formula may name — mirrors FORMULA_REFERENCEABLE_TYPES on the server.
// `calculated` is here because a formula may read another formula's answer: the chain
// is evaluated in dependency order on every write. `choice` is here because a cell
// stores its label, and the temporal two because a cell is an ISO string that AGE and
// DT_FORMAT can read.
// `email` is here because a cell is plain text with a checked grammar; `color` and
// `link` are not, because a hex code and a URL are things nobody computes over.
const FORMULA_REFERENCEABLE: ColumnType[] = [
  "number",
  "rating",
  "duration",
  "text",
  "email",
  "calculated",
  "choice",
  "date",
  "datetime",
];

interface Props {
  listUuid: string;
  workspaceUuid: string | null;
  // null => create a new column; otherwise edit this one.
  column: Column | null;
  visible: boolean;
  onHide: () => void;
  /** "convert" turns this into the first step of a type conversion: the type is
   *  chosen rather than locked, the identity fields are gone, and submitting
   *  starts a conversion instead of saving. "duplicate" is the same machinery with
   *  the other ending — it keeps the name fields, because the copy needs its own. */
  mode?: "edit" | "convert" | "duplicate";
  /** Convert mode only — the backfill report, for the caller to review. */
  onConverted?: (report: ConversionReport) => void;
  /** Called after a save that changed what a formula answers — a formula edited, or
   *  a calculated column added. The values on screen are already correct; what this
   *  gives the caller is the chance to offer a re-index, so *sorting and filtering*
   *  catch up too. Raised here rather than detected later because this is the one
   *  moment the change is known for free. */
  onFormulaChanged?: () => void;
}

/** Add, modify, or convert a column.
 *
 *  Three jobs in one dialog because they take the same settings. A column's type is
 *  immutable through a normal edit — records already hold values in its shape — so
 *  changing it goes through a **conversion**, which is why the type dropdown is
 *  locked in edit mode and open in convert mode.
 *
 *  Convert mode reuses every config control here deliberately. The target type's
 *  settings are exactly a column's settings, and the conversion validates its config
 *  from scratch against the new type, so there is nothing type-specific about the
 *  conversion form that this dialog did not already know how to draw. What it drops
 *  is the identity: a conversion keeps the column's name and uuid, so there is
 *  nothing to name. */
export function ColumnDialog({
  listUuid,
  workspaceUuid,
  column,
  visible,
  onHide,
  mode = "edit",
  onConverted,
  onFormulaChanged,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const converting = mode === "convert" && column !== null;
  const duplicating = mode === "duplicate" && column !== null;
  // Both post to the same endpoint; the presence of a name is what picks the ending.
  const copying = converting || duplicating;
  const editing = column !== null && !copying;

  const [displayName, setDisplayName] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<ColumnType>("text");
  const [required, setRequired] = useState(false);
  const [unique, setUnique] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxLength, setMaxLength] = useState<number | null>(null);
  const [min, setMin] = useState<number | null>(null);
  const [max, setMax] = useState<number | null>(null);
  // The same two config keys as `min`/`max` above, held as Dates because a
  // temporal bound is picked on a calendar rather than typed as a number. One
  // pair of keys, two pieces of state, and only the pair matching `type` is read
  // when the config is built — the same split the default value already uses.
  const [minDate, setMinDate] = useState<Date | null>(null);
  const [maxDate, setMaxDate] = useState<Date | null>(null);
  const [integerOnly, setIntegerOnly] = useState(false);
  const [choices, setChoices] = useState("");
  // Choice: whether a record may answer with something not on the list. What gets
  // typed is stored as itself, so this widens what the column accepts rather than
  // adding a value to it.
  const [allowOther, setAllowOther] = useState(false);
  const [linkText, setLinkText] = useState("");
  // Colour: the palette the picker offers first, typed as a ";"-separated list of
  // hex values — the same shorthand a choice column's options use. Offered and
  // never enforced, so an empty palette is a colour column that works normally.
  const [swatchText, setSwatchText] = useState("");
  // Phone: which country a *national* number is read against, and which countries
  // the column accepts at all. The first changes what the column takes, not just
  // how it looks — with none set the server accepts only "+" numbers.
  const [phoneRegion, setPhoneRegion] = useState<string | null>(null);
  const [phoneAllowed, setPhoneAllowed] = useState<string[]>([]);
  const [phoneDisplay, setPhoneDisplay] = useState("international");
  // Location: whether an address alone will do, where the picker opens, and which
  // map site a cell links out to. Only the first changes what the column accepts.
  const [requireCoords, setRequireCoords] = useState(false);
  const [centreLat, setCentreLat] = useState<number | null>(null);
  const [centreLng, setCentreLng] = useState<number | null>(null);
  const [mapZoom, setMapZoom] = useState<number | null>(null);
  const [mapLink, setMapLink] = useState("osm");
  // 245 entries through `Intl.DisplayNames`, computed once per module and memoised
  // here so re-rendering the dialog does not walk the list again. Labelled with
  // dial codes by the same function the record editors use, so a country reads the
  // same where a column is set up and where its cells are filled in.
  const countries = useMemo(() => countryChoices(), []);
  // The default country may only be one the column accepts. The server already
  // refuses the other combination (`validate_config_constraints`: "default_region
  // must be within allowed_regions"), so offering it here would be offering a
  // config that cannot be saved — and the refusal arrives as a toast on Save,
  // three fields away from the dropdown that caused it.
  const defaultCountries = useMemo(
    () => countryChoices(phoneAllowed as CountryCode[]),
    [phoneAllowed],
  );
  // Every example the dialog shows comes from the country actually chosen: the
  // default first, then the only country the column allows, then nothing — the
  // same order `phonePlaceholder` walks, for the same reason.
  const phoneExampleRegion = (phoneRegion ??
    (phoneAllowed.length === 1 ? phoneAllowed[0] : undefined)) as
    | CountryCode
    | undefined;
  const phoneFormats = useMemo(
    () => phoneFormatOptions(phoneExampleRegion),
    [phoneExampleRegion],
  );
  // Rating: which glyph the scale is drawn with. Its *size* is the shared `max`
  // key held above — the top of a rating is a bound like any other.
  const [ratingIcon, setRatingIcon] = useState("star");
  // Duration: how seconds are written. A display setting, like `number_format`.
  const [durationFmt, setDurationFmt] = useState("h:mm");
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
  // A lookup's saved option filter, over the *target list's* columns. Held as the
  // two halves the editor takes; recombined into a FilterGroup when saving.
  const [optionConjunction, setOptionConjunction] = useState<"and" | "or">("and");
  const [optionConditions, setOptionConditions] = useState<FilterCondition[]>([]);
  const [multiple, setMultiple] = useState(false);
  // A lookup that may create its own target rows. Purely an affordance: the
  // button it turns on is shown only to people who can already create rows in
  // the target list, and the target's own route is what enforces that.
  const [allowCreate, setAllowCreate] = useState(false);
  const [formula, setFormula] = useState("");
  const [textMode, setTextMode] = useState<TextMode>("single");
  // Default value — one piece of state per family of column type; only the one
  // matching `type` is read when the config is built.
  const [defaultText, setDefaultText] = useState(""); // text / link / email / color
  const [defaultNumber, setDefaultNumber] = useState<number | null>(null);
  // Three-valued on purpose. A checkbox column's default has three meanings and a
  // boolean can only carry two: "starts checked", "starts unchecked", and "no
  // default at all". The control used to be a single tick whose off position wrote
  // `null`, so "starts unchecked" was inexpressible — and since a missing cell and
  // a `false` one render identically, nobody noticed that `is_not_empty` and
  // `required` tell them apart. Making it a boolean and always sending it would have
  // fixed that by silently stamping `default: false` onto every existing checkbox
  // column the next time anyone opened it for an unrelated edit, which is a worse
  // trade: the seed below reads `null` as "none", so a column without a default
  // keeps not having one.
  const [defaultChecked, setDefaultChecked] = useState<boolean | null>(null);
  const [defaultChoice, setDefaultChoice] = useState<string | null>(null);
  // The same setting for a `multiple` choice column, which stores a list. Held
  // apart rather than as one union-typed piece of state, following the rule above:
  // one per family, and only the one matching the current shape is read.
  const [defaultChoices, setDefaultChoices] = useState<string[]>([]);
  const [defaultTemporal, setDefaultTemporal] = useState(""); // "" | token | SPECIFIC
  const [defaultDate, setDefaultDate] = useState<Date | null>(null); // when SPECIFIC
  const [defaultUser, setDefaultUser] = useState(""); // "" | "current_user"
  const [onUpdate, setOnUpdate] = useState(""); // "" | token (temporal / user only)

  // Clear every default/on-update control. Called when starting a new column and
  // when the type changes, so a value entered for one type can't leak into another.
  function resetDefaults() {
    setDefaultText("");
    setDefaultNumber(null);
    setDefaultChecked(null);
    setDefaultChoice(null);
    setDefaultChoices([]);
    setDefaultTemporal("");
    setDefaultDate(null);
    setDefaultUser("");
    setOnUpdate("");
  }

  // Seed the form from the column being edited (or clear it for a new one) each
  // time the dialog opens.
  useEffect(() => {
    if (!visible) return;
    // A conversion builds a *new* column's config from scratch — the old type's
    // keys mean nothing to the new one, and the server validates the new config
    // without merging — so it starts blank rather than from what is stored. The
    // one thing it inherits is `required`/`unique`, which are about the field
    // rather than the type, and which the person converting most likely wants kept.
    if (converting) {
      const config = column.config ?? {};
      setDisplayName(column.display_name);
      setName(column.name);
      // The first type this column can actually become — "text" would be wrong
      // for a text column, which is the one case it is not a legal target.
      setType(
        conversionTargets(column.type).find((o) => !o.disabled)?.value ?? "text",
      );
      setRequired(!!config.required);
      setUnique(!!config.unique);
      setMaxLength(null);
      setMin(null);
      setMax(null);
      setMinDate(null);
      setMaxDate(null);
      setIntegerOnly(false);
      setChoices("");
      setAllowOther(false);
      setLinkText("");
      setSwatchText("");
      setRatingIcon("star");
      setDurationFmt("h:mm");
      setPhoneRegion(null);
      setPhoneAllowed([]);
      setPhoneDisplay("international");
      setRequireCoords(false);
      setCentreLat(null);
      setCentreLng(null);
      setMapZoom(null);
      setMapLink("osm");
      setTargetWorkspace(workspaceUuid);
      setTargetListUuid(null);
      setDisplayColumnUuid(null);
      setParentColumnUuid(null);
      setTargetFilterColumnUuid(null);
      setMultiple(false);
      setAllowCreate(false);
      setFormula("");
      setTextMode("single");
      // The config starts blank, so the only advanced setting that can arrive here
      // is the `unique` carried over just above.
      setAdvancedOpen(hasAdvancedConfig({ unique: config.unique }));
      resetDefaults();
      return;
    }
    if (!column) {
      setDisplayName("");
      setName("");
      setType("text");
      setRequired(false);
      setUnique(false);
      setMaxLength(null);
      setMin(null);
      setMax(null);
      setMinDate(null);
      setMaxDate(null);
      setIntegerOnly(false);
      setChoices("");
      setAllowOther(false);
      setLinkText("");
      setSwatchText("");
      setRatingIcon("star");
      setDurationFmt("h:mm");
      setPhoneRegion(null);
      setPhoneAllowed([]);
      setPhoneDisplay("international");
      setRequireCoords(false);
      setCentreLat(null);
      setCentreLng(null);
      setMapZoom(null);
      setMapLink("osm");
      setTargetWorkspace(workspaceUuid);
      setTargetListUuid(null);
      setDisplayColumnUuid(null);
      setParentColumnUuid(null);
      setTargetFilterColumnUuid(null);
      setMultiple(false);
      setAllowCreate(false);
      setFormula("");
      setTextMode("single");
      setAdvancedOpen(false);
      resetDefaults();
      return;
    }
    const config = column.config ?? {};
    setDisplayName(column.display_name);
    setName(column.name);
    setType(column.type);
    setRequired(!!config.required);
    setUnique(!!config.unique);
    // A setting that is set must not be a setting that is invisible, so the section
    // opens itself for a column that uses anything inside it.
    setAdvancedOpen(hasAdvancedConfig(config));
    setMaxLength((config.max_length as number) ?? null);
    setMin((config.min as number) ?? null);
    setMax((config.max as number) ?? null);
    // The same keys read the other way round. `parseTemporal` answers null for a
    // bound this column has not got, and for one stored in a shape it cannot
    // read — a malformed bound leaves the picker empty rather than showing an
    // "Invalid Date", exactly as a malformed cell does.
    setMinDate(parseTemporal(column.type, config.min));
    setMaxDate(parseTemporal(column.type, config.max));
    setIntegerOnly(!!config.integer_only);
    setChoices(((config.options as string[]) ?? []).join(";"));
    setAllowOther(!!config.allow_other);
    setLinkText((config.label as string) ?? "");
    setSwatchText(((config.swatches as string[]) ?? []).join(";"));
    setRequireCoords(!!config.require_coordinates);
    setCentreLat((config.default_center?.lat as number) ?? null);
    setCentreLng((config.default_center?.lng as number) ?? null);
    setMapZoom((config.default_zoom as number) ?? null);
    setMapLink((config.map_link as string) ?? "osm");
    setPhoneRegion((config.default_region as string) ?? null);
    setPhoneAllowed((config.allowed_regions as string[]) ?? []);
    setPhoneDisplay((config.display_format as string) ?? "international");
    setRatingIcon((config.rating_icon as string) ?? "star");
    setDurationFmt((config.duration_format as string) ?? "h:mm");
    // Left null so the seeding effect can resolve which workspace the stored
    // target lives in (the config only records the list, not its workspace).
    setTargetWorkspace(null);
    setTargetListUuid((config.target_list_uuid as string) ?? null);
    setDisplayColumnUuid((config.display_column_uuid as string) ?? null);
    setParentColumnUuid((config.parent_column_uuid as string) ?? null);
    setTargetFilterColumnUuid(
      (config.target_filter_column_uuid as string) ?? null,
    );
    setOptionConjunction(config.option_filters?.conjunction ?? "and");
    setOptionConditions(config.option_filters?.conditions ?? []);
    setMultiple(!!config.multiple);
    setAllowCreate(!!config.allow_create);
    setFormula((config.formula as string) ?? "");
    // Unset is single line — the shape every text column had before text_mode.
    setTextMode(
      config.text_mode === "multiline" || config.text_mode === "rich"
        ? config.text_mode
        : "single",
    );

    // Seed the default/on-update controls from the stored config.
    resetDefaults();
    const def = config.default;
    if (typeof def === "string") setDefaultText(def);
    if (typeof def === "number") setDefaultNumber(def);
    setDefaultChecked(typeof def === "boolean" ? def : null);
    // A choice default is stored as the option itself — as a list of them on a
    // `multiple` column, which is the shape the server validates it in.
    if (typeof def === "string") setDefaultChoice(def);
    if (Array.isArray(def)) setDefaultChoices(def as string[]);
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

    // A duplicate keeps the source's settings — that is what makes it a copy —
    // but must not keep its name, which is taken. `_copy` matches how cloning a
    // list names its result.
    if (duplicating) {
      setName(`${column.name}_copy`.slice(0, 120));
      setDisplayName(`${column.display_name} (copy)`);
    }
  }, [visible, column, workspaceUuid, converting, duplicating]);

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
  //
  // `lookup_targets=true` on the shared query adds the lists granted `lookup` but
  // not `view` — shared *only* as a lookup target, so absent from every other
  // listing by design. Without it they are unreachable: the picker would not name
  // them and the column could not be built. They are always in someone else's
  // workspace (you hold `view` on your own), so the owned-workspace query has no
  // use for the flag.
  const lists = useQuery({
    queryKey: ["lookup-target-lists", targetWorkspace],
    queryFn: () =>
      api.get<ListRead[]>(
        targetWorkspace === SHARED
          ? `/lists/?shared=true&lookup_targets=true&limit=1000`
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
  //
  // A target shared only as a lookup target (granted `lookup`, not `view`) refuses
  // this: `lookup` hands over row labels, not the target's schema. So it can 403,
  // and that is a normal state rather than an error to retry — the label column and
  // the nesting pair simply cannot be chosen, and the server's own fallback (the
  // target's primary column, else its first) supplies the label.
  const targetColumns = useQuery({
    queryKey: ["columns", targetListUuid],
    queryFn: () => api.get<Column[]>(`/lists/${targetListUuid}/columns/`),
    enabled: type === "lookup" && !!targetListUuid,
    retry: false,
  });
  const targetSchemaHidden = targetColumns.isError;
  // This list's own columns — for a formula, the columns it may reference; for a
  // lookup, the sibling lookups it could nest under.
  const ownColumns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
    enabled: type === "calculated" || type === "lookup",
  });
  // Never *this* column, on top of the type gate: naming itself is a cycle of length
  // one, and the server refuses it.
  const referenceableNames = (ownColumns.data ?? [])
    .filter(
      (c) => FORMULA_REFERENCEABLE.includes(c.type) && c.uuid !== column?.uuid,
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
    (type === "user" ||
      type === "group" ||
      type === "lookup" ||
      type === "choice") &&
    multiple;
  // A calculated column's only writer is its formula, so `required` has nothing to
  // act on — there is no value for anyone to leave blank. `Unique` does: it asks
  // whether two rows may compute the same answer, which is how a formula expresses
  // a key no single column holds. So the section is shown, minus Required.
  const isCalculated = type === "calculated";

  // Types that offer an auto-stamp on write (a "last modified" date, "edited by" user).
  const supportsOnUpdate = isTemporal(type) || type === "user";
  // The default is hidden once an on-update stamp is set: the stamp always
  // overwrites, so a default could never take effect, and showing both misleads.
  const showDefault =
    !isCalculated &&
    type !== "lookup" &&
    type !== "group" &&
    // A location is picked on a map, and a default one is a place every new
    // record starts at — which is what `default_center` already expresses,
    // without writing a value into cells nobody chose.
    type !== "location" &&
    !(supportsOnUpdate && onUpdate !== "");
  // Options for the choice "default option" dropdown, from the shorthand as typed.
  const choiceLabels = choices
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);

  // One mutation, two response shapes: saving returns the column, converting
  // returns a report on a column nobody can see yet.
  const save = useMutation<Column | ConversionReport>({
    mutationFn: () => {
      // `unique` is always sent, never omitted: a config PATCH merges onto the
      // stored one, so leaving it out would preserve a `true` this dialog is
      // currently hiding — a constraint still enforced but no longer visible.
      const config: Record<string, unknown> = isCalculated
        ? { formula: formula.trim(), unique }
        : {
            required,
            unique: multiValued ? false : unique,
          };
      if (type === "text") {
        config.max_length = maxLength;
        // Always sent, for the same reason as `unique`: the PATCH merges, so an
        // omitted key would keep a stored "multiline" and make the switch back
        // to a single line impossible.
        config.text_mode = textMode;
      }
      if (type === "number") {
        config.min = min;
        config.max = max;
        config.integer_only = integerOnly;
      }
      if (isTemporal(type)) {
        // Always sent, like `unique`: a config PATCH merges, so clearing a bound
        // means writing an explicit null rather than omitting the key.
        config.min = minDate ? serializeTemporal(type, minDate) : null;
        config.max = maxDate ? serializeTemporal(type, maxDate) : null;
      }
      if (type === "choice") {
        config.choices = choices;
        // Always sent, like `unique`: a config PATCH merges, so omitting it would
        // keep a stored `true` and leave the column still taking answers the
        // dialog now says it does not.
        config.allow_other = allowOther;
        config.multiple = multiple;
      }
      if (type === "link") config.label = linkText.trim() || null;
      if (type === "email") config.max_length = maxLength;
      if (type === "location") {
        // All four always sent, like `unique`: a config PATCH merges, so clearing a
        // centre means an explicit null rather than an omitted key. The centre is
        // both coordinates or neither — the server refuses half of one, which is
        // right, and this is what stops the dialog ever sending half.
        config.require_coordinates = requireCoords;
        config.default_center =
          centreLat !== null && centreLng !== null
            ? { lat: centreLat, lng: centreLng }
            : null;
        config.default_zoom = mapZoom;
        config.map_link = mapLink === "osm" ? null : mapLink;
      }
      if (type === "phone") {
        // All three always sent, like `unique`: a config PATCH merges, so clearing
        // a region means an explicit null rather than an omitted key — and this is
        // the one folded-away setting where leaving a stale value in place changes
        // which numbers the column *accepts*, not merely how they are drawn.
        config.default_region = phoneRegion;
        config.allowed_regions = phoneAllowed.length ? phoneAllowed : null;
        config.display_format =
          phoneDisplay === "international" ? null : phoneDisplay;
      }
      if (type === "rating") {
        // The scale rides on the shared `max` key, so it goes out with the other
        // bounds rather than under a name of its own. Always sent, like `unique`:
        // a config PATCH merges, so a scale reset to the default has to be an
        // explicit value rather than an omitted key.
        config.max = max;
        // `null` at the default, the way a link's empty `label` is sent as null:
        // these are folded-away settings, and `hasAdvancedConfig` counts anything
        // stored as a choice somebody made — so writing "star" onto every rating
        // column would open the Advanced section on all of them, over nothing.
        config.rating_icon = ratingIcon === "star" ? null : ratingIcon;
      }
      if (type === "duration") {
        config.min = min;
        config.max = max;
        config.duration_format = durationFmt === "h:mm" ? null : durationFmt;
      }
      if (type === "color") {
        // Always sent, like `unique`: a config PATCH merges, so emptying the
        // palette means writing an explicit null rather than omitting the key.
        const swatches = swatchText
          .split(";")
          .map((s) => s.trim())
          .filter(Boolean);
        config.swatches = swatches.length ? swatches : null;
      }
      if (type === "user" || type === "group") config.multiple = multiple;
      if (type === "lookup") {
        config.target_list_uuid = targetListUuid;
        config.display_column_uuid = displayColumnUuid;
        config.multiple = multiple;
        // Always sent, for the merge reason above: omitting it would leave a
        // stored `true` in place and keep offering a button the dialog now says
        // is off.
        config.allow_create = allowCreate;
        // Always sent, like `unique`: a config PATCH merges, so un-nesting
        // requires explicit nulls rather than omitted keys.
        config.parent_column_uuid = parentColumnUuid;
        config.target_filter_column_uuid = parentColumnUuid
          ? targetFilterColumnUuid
          : null;
        // Always sent, for the same merge reason. `null` rather than an empty
        // group when there is nothing to apply: `hasAdvancedConfig` counts any
        // object as a setting somebody chose, so an empty one would open the
        // advanced section on every lookup column forever.
        const conditions = cleanConditions(optionConditions);
        config.option_filters = conditions.length
          ? { conjunction: optionConjunction, conditions }
          : null;
      }

      // Default value — sent for every type that supports one (not lookup, group,
      // or calculated). Always sent, even when empty, so clearing it on edit takes
      // effect: a config PATCH merges, so an omitted key would keep the old default.
      if (
        !isCalculated &&
        type !== "lookup" &&
        type !== "group" &&
        type !== "location"
      ) {
        let defaultValue: unknown = null;
        if (
          type === "text" ||
          type === "link" ||
          type === "email" ||
          type === "phone" ||
          type === "color"
        )
          // Sent as typed. The server normalises an email and a colour to their
          // stored form (`_validate_config`), so a default and a typed cell end
          // up as one value rather than two spellings of one.
          defaultValue = defaultText.trim() || null;
        else if (
          type === "number" ||
          type === "rating" ||
          type === "duration"
        )
          // A duration default is counted in seconds like the cells, and a
          // rating default is a point on the scale.
          defaultValue = defaultNumber;
        else if (type === "checkbox")
          // `defaultChecked`, not `defaultChecked ? true : null`. Unticking used to
          // write `null`, so "new records start unchecked" was inexpressible — the
          // only two states the dialog could produce were "starts checked" and "no
          // default at all". They render identically (`renderCell` draws a missing
          // cell and a `false` one the same way), which is why it went unnoticed,
          // but `is_not_empty` and `required` both disagree with that reading: a
          // record whose box is deliberately unticked has an answer, and a record
          // that was never given one does not.
          defaultValue = defaultChecked;  // true, false, or null for none
        else if (type === "choice")
          // A `multiple` column's cells hold a list, and so must its default —
          // `validate_scalar` refuses a bare string on one.
          defaultValue = multiple
            ? defaultChoices.length
              ? defaultChoices
              : null
            : defaultChoice || null;
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

      if (copying) {
        // Same endpoint for both; sending a name is what makes the server keep the
        // source and put the new column beside it. A conversion sends none, because
        // it keeps the column's identity and so has nothing to rename. Either way
        // this is a dry run: it writes nothing and creates nothing, so the report
        // it returns costs nothing to walk away from.
        return api.post<ConversionReport>(
          `/lists/${listUuid}/columns/${column.uuid}/convert`,
          duplicating
            ? { type, config, name, display_name: displayName }
            : { type, config },
        );
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
      if (copying) {
        // Deliberately no cache invalidation: the list is unchanged until the
        // conversion is applied, and refetching here would suggest otherwise.
        onConverted?.(saved as ConversionReport);
        onHide();
        return;
      }
      queryClient.invalidateQueries({ queryKey: ["columns", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      // Turning `multiple` off rewrites records, and the labels a lookup/user cell
      // renders are keyed on the values it holds.
      queryClient.invalidateQueries({ queryKey: ["lookup-labels"] });
      queryClient.invalidateQueries({ queryKey: ["principal-labels"] });
      const column_ = saved as Column;
      toast.success(editing ? "Column updated" : "Column added", column_.display_name);
      onHide();
      // The stored index is what a formula change leaves behind — the grid is right
      // the moment this returns, because a calculated cell is recomputed on read.
      // Compared against the *stored* formula rather than tracked as the field is
      // typed, so re-saving an untouched formula asks for nothing.
      if (column_.type === "calculated") {
        const before = editing ? ((column?.config?.formula as string) ?? "") : "";
        if (formula.trim() !== before.trim()) onFormulaChanged?.();
      }
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

  /**
   * Which types have configuration that stays on screen — the difference between an
   * "Options" section and no section at all rather than an empty heading over nothing.
   *
   * Narrower than it was, because most type-specific settings have moved behind
   * "Advanced". What is left is what the type cannot be understood without, and — for
   * a lookup and a calculated column — what the Save button refuses to proceed
   * without. A number column's min and max, a link's display text and a principal
   * column's "allow several" are all refinements of a type that reads fine without
   * them, so those types no longer have an Options section at all.
   */
  const hasOptions =
    isCalculated || ["text", "choice", "lookup"].includes(type);

  return (
    <AppDialog
      header={
        converting
          ? `Convert “${column.display_name}”`
          : duplicating
            ? `Duplicate “${column.display_name}”`
            : editing
              ? `Edit “${column.display_name}”`
              : "Add column"
      }
      visible={visible}
      onHide={onHide}
      style={{ width: 540 }}
      footer={
        <>
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={
              converting
                ? "Convert"
                : duplicating
                  ? "Duplicate"
                  : editing
                    ? "Save"
                    : "Add column"
            }
            icon={
              converting
                ? "pi pi-sync"
                : duplicating
                  ? "pi pi-copy"
                  : editing
                    ? "pi pi-check"
                    : "pi pi-plus"
            }
            onClick={onSave}
            loading={save.isPending}
            disabled={
              // Identity is not editable in a conversion, so it cannot be invalid.
              // A duplicate names a new column, so it is checked as usual.
              (!converting && (!displayName.trim() || !isValidName(name))) ||
              (type === "lookup" && !targetListUuid) ||
              // A half-configured nesting: the pairing is set together or not at all.
              (type === "lookup" && !!parentColumnUuid && !targetFilterColumnUuid) ||
              (isCalculated && !formula.trim())
            }
          />
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <ErrorMessage error={save.error} />

        {copying && (
          <Message
            severity="info"
            className="w-full"
            content={
              <span className="text-sm">
                Nothing changes yet. This works out what every value would become
                and shows you what did and did not convert; nothing is written
                until you decide.
                {duplicating
                  ? ` “${column.display_name}” is left exactly as it is.`
                  : ""}
              </span>
            }
          />
        )}

        <FormSection title={converting ? "Convert to" : "Basics"}>
          {/* No name fields when converting: the column keeps its name, its uuid
              and everything pointing at it — that is what makes the cutover safe. */}
          {!converting && (
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
          )}
          <Field
            label={converting ? "New type" : duplicating ? "Type of the copy" : "Type"}
            htmlFor="col-type"
            hint={
              editing
                ? "A column's type cannot change: existing records already hold values in its shape."
                : copying
                  ? "Greyed-out types need two steps — convert to Text first, then to the type you want."
                  : undefined
            }
          >
            <Dropdown
              inputId="col-type"
              value={type}
              options={
                converting
                  ? conversionTargets(column.type)
                  : duplicating
                    ? duplicateTargets(column.type)
                    : COLUMN_TYPE_OPTIONS
              }
              optionDisabled="disabled"
              onChange={(e) => {
                setType(e.value as ColumnType);
                // The default/on-update controls are type-specific.
                resetDefaults();
              }}
              disabled={editing}
              className="w-full"
            />
          </Field>
        </FormSection>

        {hasOptions && (
          <FormSection title="Options">
            {type === "text" && (
              <>
                <Field
                  label="Input"
                  htmlFor="col-textmode"
                  // Deliberately not disabled while editing, unlike the type
                  // above: every mode stores a string, so switching is a config
                  // change and existing records are already in the right shape.
                  hint={
                    textMode === "rich"
                      ? "A small editor with bold, italic, lists, quotes and links. Existing plain text is kept, and switching back to plain shows the words without their formatting."
                      : "How the field is filled in. Both store plain text, so this can be changed later without touching existing records."
                  }
                >
                  <SelectButton
                    id="col-textmode"
                    value={textMode}
                    options={TEXT_MODE_OPTIONS}
                    onChange={(e) => e.value && setTextMode(e.value as TextMode)}
                    allowEmpty={false}
                  />
                </Field>
              </>
            )}

            {type === "choice" && (
              <Field
                label="Choices"
                htmlFor="col-choices"
                hint={
                  editing
                    ? 'Separated by ";". Renaming an option keeps records pointing at it; removing one leaves records holding a value the column no longer offers.'
                    : 'Separated by ";".'
                }
              >
                <InputText
                  id="col-choices"
                  value={choices}
                  onChange={(e) => setChoices(e.target.value)}
                  placeholder="Todo;Doing;Done"
                />
              </Field>
            )}

            {type === "lookup" && (
              <>
                <Field
                  label="Workspace"
                  htmlFor="col-target-ws"
                  hint="Point at a list in any of your workspaces, or one shared with you."
                >
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
                      // The list, its display column and any nesting all belong
                      // to the workspace being left; clear them so nothing dangles.
                      setTargetListUuid(null);
                      setDisplayColumnUuid(null);
                      setParentColumnUuid(null);
                      setTargetFilterColumnUuid(null);
                    }}
                    placeholder={
                      myWorkspaces.isLoading ? "Loading…" : "Select a workspace…"
                    }
                    filter
                    className="w-full"
                  />
                </Field>
                <Field
                  label="Target list"
                  htmlFor="col-target"
                  hint={
                    targetListUuid && targetSchemaHidden
                      ? "This list is shared with you as a lookup target only, so its columns aren’t yours to browse. Rows will be labelled by the list’s own default column."
                      : undefined
                  }
                >
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
                      // So is an option filter: its conditions name the old
                      // target's columns, which the new one does not have. Kept
                      // they would every one be dropped server-side, leaving a
                      // filter that reads as set and offers everything.
                      setOptionConditions([]);
                    }}
                    placeholder={lists.isLoading ? "Loading…" : "Select a list…"}
                    filter
                    className="w-full"
                  />
                </Field>
                {targetListUuid && !targetSchemaHidden && (
                  <Field label="Column to show" htmlFor="col-display">
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
                      className="w-full"
                    />
                  </Field>
                )}
              </>
            )}

            {isCalculated && (
              <Field
                label="Formula"
                htmlFor="col-formula"
                hint={
                  <>
                    Computed from other columns whenever the record is saved, and
                    again on every read. Numbers: <code>+ - * /</code>
                    , parentheses, <code>MIN</code>, <code>MAX</code>,{" "}
                    <code>ROUND</code>, <code>ABS</code>, <code>INT</code>. Text:{" "}
                    <code>CONCAT</code>, <code>UPPER</code>, <code>LOWER</code>,{" "}
                    <code>TRIM</code>, <code>LEN</code>, <code>LEFT</code>,{" "}
                    <code>RIGHT</code>, <code>REPLACE</code>. Dates:{" "}
                    <code>AGE(date)</code> for years old,{" "}
                    <code>DT_FORMAT(date, "%d %b %Y")</code> to write one out, and{" "}
                    <code>TODAY()</code> / <code>NOW()</code> for the current date and
                    time — <code>IF(due &lt; TODAY(), "Overdue", "Open")</code>. And{" "}
                    <code>IF(cond, a, b)</code>, with{" "}
                    <code>DEFAULT(x, fallback)</code> to fill an empty cell —{" "}
                    <code>price - DEFAULT(discount, 0)</code> — and{" "}
                    <code>IS_NULL(x)</code> to ask about one.
                    {referenceableNames.length > 0
                      ? ` Columns you can reference: ${referenceableNames.join(", ")}.`
                      : " Add a number, text, choice or date column first — a formula references those."}
                    {" A formula may also read another calculated column, as long as"}
                    {" the two do not end up depending on each other."}
                  </>
                }
              >
                <InputTextarea
                  id="col-formula"
                  value={formula}
                  onChange={(e) => setFormula(e.target.value)}
                  rows={2}
                  placeholder="qty * price"
                  style={{ fontFamily: "monospace" }}
                />
              </Field>
            )}

          </FormSection>
        )}

        {/* Required stays on screen while Unique folds away, and the split is not
            arbitrary: "must be filled in" is a question nearly every column has an
            answer to, and it is the one piece of validation that changes whether a
            record can be saved at all. A calculated column has no say — its formula
            is its only writer — so it gets no section rather than an empty one. */}
        {!isCalculated && (
          <FormSection title="Validation">
            <CheckboxField
              inputId="col-required"
              checked={required}
              onChange={setRequired}
              label="Required"
              hint="A record cannot be saved with this one left blank."
            />
          </FormSection>
        )}

        {/* Everything a column can be refined with, folded away by default.
            Nothing the Save button validates is in here (see `columnAdvanced.ts`),
            and the section opens itself for a column that already uses any of it. */}
        <FormSection
          title="Advanced"
          collapsible
          open={advancedOpen}
          onOpenChange={setAdvancedOpen}
        >
          {(type === "text" || type === "email") && (
            <Field label="Max length" htmlFor="col-maxlen" optional>
              <InputNumber
                inputId="col-maxlen"
                value={maxLength}
                onValueChange={(e) => setMaxLength(e.value ?? null)}
                min={1}
                useGrouping={false}
              />
            </Field>
          )}

          {(type === "number" || type === "duration") && (
            <>
              <FieldRow>
                {/* A duration's bounds are the same two config keys, counted in
                    seconds — the unit the cells hold. */}
                <Field
                  label={type === "duration" ? "Min (seconds)" : "Min"}
                  htmlFor="col-min"
                  optional
                >
                  <InputNumber
                    inputId="col-min"
                    value={min}
                    onValueChange={(e) => setMin(e.value ?? null)}
                    useGrouping={false}
                  />
                </Field>
                <Field
                  label={type === "duration" ? "Max (seconds)" : "Max"}
                  htmlFor="col-max"
                  optional
                >
                  <InputNumber
                    inputId="col-max"
                    value={max}
                    onValueChange={(e) => setMax(e.value ?? null)}
                    useGrouping={false}
                  />
                </Field>
              </FieldRow>
              {type === "number" && (
                <CheckboxField
                  inputId="col-int"
                  checked={integerOnly}
                  onChange={setIntegerOnly}
                  label="Integers only"
                />
              )}
            </>
          )}

          {isTemporal(type) && (
            <FieldRow>
              {/* "Earliest"/"Latest" rather than the number column's "Min"/"Max":
                  they mean the same thing to the same two config keys, and this is
                  how a person says it about a date. Leaving one empty bounds the
                  column on one side only. */}
              <Field
                label={type === "time" ? "Earliest time" : "Earliest"}
                htmlFor="col-min"
                optional
              >
                <Calendar
                  inputId="col-min"
                  value={minDate}
                  onChange={(e) => setMinDate((e.value as Date) ?? null)}
                  dateFormat="yy-mm-dd"
                  timeOnly={type === "time"}
                  showTime={type === "datetime"}
                  showButtonBar
                />
              </Field>
              <Field
                label={type === "time" ? "Latest time" : "Latest"}
                htmlFor="col-max"
                optional
              >
                <Calendar
                  inputId="col-max"
                  value={maxDate}
                  onChange={(e) => setMaxDate((e.value as Date) ?? null)}
                  dateFormat="yy-mm-dd"
                  timeOnly={type === "time"}
                  showTime={type === "datetime"}
                  showButtonBar
                />
              </Field>
            </FieldRow>
          )}

          {type === "choice" && (
            <CheckboxField
              inputId="col-multi-choice"
              checked={multiple}
              onChange={(next) => {
                setMultiple(next);
                // Carry the default across the two shapes rather than dropping
                // it — the same option means the same thing either way, and the
                // control it lives in is about to be swapped out from under it.
                // The rule is `coerce_shape`'s: first element on the way down,
                // one-element list on the way up.
                if (next) setDefaultChoices(defaultChoice ? [defaultChoice] : []);
                else setDefaultChoice(defaultChoices[0] ?? null);
              }}
              label="Allow selecting multiple options"
              hint={
                // Says what turning it *off* costs, because that is the only
                // direction that loses anything and the loss is not immediate:
                // reads keep the first value from that moment, and the rest sit
                // on the records until one is next edited or the nightly
                // reconcile converges them. Saving that way asks first (see
                // `onSave`), so this is the warning before the warning.
                editing && multiple
                  ? "Records hold a list of options. Turning this off keeps only the first value of each record — until they are next edited, switching back restores the rest."
                  : "Records may hold several options at once, stored as a list."
              }
            />
          )}

          {type === "choice" && (
            <CheckboxField
              inputId="col-allow-other"
              checked={allowOther}
              onChange={setAllowOther}
              label="Allow an “Other” answer"
              hint={
                // Says what it stores, because that is the surprise: an "Other"
                // answer is not tagged as one anywhere. It is simply the words
                // that were typed, so it groups, filters and exports as its own
                // value rather than alongside the other write-ins.
                editing && allowOther
                  ? "Records may answer with something not on the list, typed in a box below the choices. Turning this off leaves those answers on the records that hold them, but the next edit of such a record must pick one of the options."
                  : "Records may answer with something not on the list, typed in a box below the choices. What they type is stored as the value itself, so two people writing the same thing differently stay two different values."
              }
            />
          )}

          {type === "link" && (
            <Field label="Link text" htmlFor="col-linktext" optional>
              <InputText
                id="col-linktext"
                value={linkText}
                onChange={(e) => setLinkText(e.target.value)}
                placeholder="Shown instead of the URL"
              />
            </Field>
          )}

            {type === "rating" && (
              <FieldRow>
                <Field
                  label="Scale"
                  htmlFor="col-rating-max"
                  optional
                  hint="How many to offer. Ratings start at 1; an unrated record is an empty cell."
                >
                  <InputNumber
                    inputId="col-rating-max"
                    value={max}
                    onValueChange={(e) => setMax(e.value ?? null)}
                    min={1}
                    max={RATING_MAX}
                    placeholder="5"
                    useGrouping={false}
                  />
                </Field>
                <Field label="Shown as" htmlFor="col-rating-icon">
                  <Dropdown
                    inputId="col-rating-icon"
                    value={ratingIcon}
                    options={RATING_ICON_OPTIONS}
                    onChange={(e) => setRatingIcon(e.value)}
                  />
                </Field>
              </FieldRow>
            )}

            {type === "duration" && (
              <Field
                label="Written as"
                htmlFor="col-duration-format"
                hint="How durations are shown and typed. A cell always stores seconds, so this can be changed later and an export is unaffected."
              >
                <Dropdown
                  inputId="col-duration-format"
                  value={durationFmt}
                  options={DURATION_FORMAT_OPTIONS}
                  onChange={(e) => setDurationFmt(e.value)}
                />
              </Field>
            )}

          {type === "location" && (
            <>
              <CheckboxField
                inputId="col-require-coords"
                checked={requireCoords}
                onChange={setRequireCoords}
                label="Require a pin, not just an address"
                hint="A record cannot be saved with an address that was never placed on the map."
              />
              <FieldRow>
                <Field label="Map opens at (latitude)" htmlFor="col-centre-lat" optional>
                  <InputNumber
                    inputId="col-centre-lat"
                    value={centreLat}
                    onValueChange={(e) => setCentreLat(e.value ?? null)}
                    minFractionDigits={0}
                    maxFractionDigits={5}
                    min={-90}
                    max={90}
                    useGrouping={false}
                  />
                </Field>
                <Field label="Longitude" htmlFor="col-centre-lng" optional>
                  <InputNumber
                    inputId="col-centre-lng"
                    value={centreLng}
                    onValueChange={(e) => setCentreLng(e.value ?? null)}
                    minFractionDigits={0}
                    maxFractionDigits={5}
                    min={-180}
                    max={180}
                    useGrouping={false}
                  />
                </Field>
              </FieldRow>
              <Field
                label="Zoom"
                htmlFor="col-map-zoom"
                optional
                hint="0 is the whole world, 19 is a building. Left empty, the picker opens on the world until a centre is set."
              >
                <InputNumber
                  inputId="col-map-zoom"
                  value={mapZoom}
                  onValueChange={(e) => setMapZoom(e.value ?? null)}
                  min={0}
                  max={20}
                  useGrouping={false}
                />
              </Field>
              <Field label="Open in" htmlFor="col-map-link">
                <Dropdown
                  inputId="col-map-link"
                  value={mapLink}
                  options={MAP_LINK_OPTIONS}
                  onChange={(e) => setMapLink(e.value)}
                />
              </Field>
            </>
          )}

          {type === "phone" && (
            <>
              <Field
                label="Default country"
                htmlFor="col-phone-region"
                optional
                hint={
                  phoneAllowed.length
                    ? "Which country a local number belongs to. Only the countries this column accepts are offered."
                    : "Which country a local number like 0917 123 4567 belongs to. Leave it unset and whoever fills the column picks a country per number, or writes the country code."
                }
              >
                <Dropdown
                  inputId="col-phone-region"
                  value={phoneRegion}
                  options={defaultCountries}
                  onChange={(e) => setPhoneRegion(e.value ?? null)}
                  filter
                  showClear
                  placeholder="None — chosen per number"
                />
              </Field>
              <Field
                label="Only accept numbers from"
                htmlFor="col-phone-allowed"
                optional
                hint="Leave empty to accept any country."
              >
                <MultiSelect
                  inputId="col-phone-allowed"
                  value={phoneAllowed}
                  options={countries}
                  onChange={(e) => {
                    const next: string[] = e.value ?? [];
                    setPhoneAllowed(next);
                    // Narrowing the column out from under its own default would
                    // save a config the server refuses. Cleared here, where the
                    // cause is on screen, rather than reported on Save.
                    if (next.length && phoneRegion && !next.includes(phoneRegion)) {
                      setPhoneRegion(null);
                    }
                  }}
                  filter
                  display="chip"
                  placeholder="Any country"
                />
              </Field>
              <Field
                label="Shown as"
                htmlFor="col-phone-display"
                hint={
                  phoneExampleRegion
                    ? `Numbers will look like ${phoneExample(
                        phoneExampleRegion,
                        phoneDisplay as PhoneFormat,
                      )} in the grid. It changes nothing about what is stored — a cell always holds E.164.`
                    : "How a number is drawn in the grid. It changes nothing about what is stored — a cell always holds E.164. Set a default country above to see an example."
                }
              >
                <Dropdown
                  inputId="col-phone-display"
                  value={phoneDisplay}
                  options={phoneFormats}
                  onChange={(e) => setPhoneDisplay(e.value)}
                />
              </Field>
            </>
          )}

          {type === "color" && (
            <Field
              label="Palette"
              htmlFor="col-swatches"
              optional
              hint="Colours the picker offers first, separated by “;”. It is a shortcut and not a restriction — any colour is still a valid answer."
            >
              <InputText
                id="col-swatches"
                className="font-mono"
                value={swatchText}
                onChange={(e) => setSwatchText(e.target.value)}
                placeholder="#1d4ed8;#dc2626;#16a34a"
              />
            </Field>
          )}

          {(type === "user" || type === "group") && (
            <CheckboxField
              inputId="col-multi-principal"
              checked={multiple}
              onChange={setMultiple}
              label={`Allow selecting multiple ${
                type === "user" ? "users" : "groups"
              }`}
            />
          )}

          {type === "lookup" && (
            <>
              {targetListUuid &&
                (parentOptions.length > 0 || parentColumnUuid) && (
                  <>
                    <Field
                      label="Nest under"
                      htmlFor="col-nest-parent"
                      optional
                      hint="What's selected there decides which rows this column offers — pick a country first, get only its cities."
                    >
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
                          // The match column depends on the parent; re-derive
                          // it, and save a click when only one column qualifies.
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
                        className="w-full"
                      />
                    </Field>
                    {parentColumnUuid && (
                      <Field
                        label="Match on"
                        htmlFor="col-nest-match"
                        hint={`The target list's column that links each row to a ${
                          parentColumn?.display_name ?? "parent"
                        } selection.`}
                      >
                        <Dropdown
                          inputId="col-nest-match"
                          value={targetFilterColumnUuid}
                          options={matchOptions.map((c) => ({
                            label: c.display_name,
                            value: c.uuid,
                          }))}
                          onChange={(e) =>
                            setTargetFilterColumnUuid(
                              (e.value as string) ?? null,
                            )
                          }
                          placeholder="Select a column…"
                          className="w-full"
                        />
                      </Field>
                    )}
                  </>
                )}
              {/* Hidden when the target is shared as a lookup target only: its
                  columns are refused (`targetSchemaHidden`), and there is no
                  filter to build without them. Not worth working around — that
                  refusal is what the `lookup` grant is, and a stored filter set
                  by someone who *can* read the schema keeps applying regardless
                  of who opens this dialog. */}
              {targetListUuid && !targetSchemaHidden && (
                <Field
                  label="Limit options to"
                  optional
                  hint="Rows of the target list that don't match are never offered. Records already pointing at one keep it, and still show its name."
                >
                  <FilterConditionsEditor
                    listUuid={targetListUuid}
                    columns={targetColumns.data ?? []}
                    conjunction={optionConjunction}
                    onConjunctionChange={setOptionConjunction}
                    conditions={optionConditions}
                    onConditionsChange={setOptionConditions}
                  />
                </Field>
              )}
              <CheckboxField
                inputId="col-multiple"
                checked={multiple}
                onChange={setMultiple}
                label="Allow linking multiple rows"
              />
              {/* Shown for every lookup, including one whose target is shared as a
                  lookup target only. Whether *you* can add rows over there is not
                  the question this setting answers — it is offered to each person
                  filling the form on their own access, which may well be wider
                  than the access of whoever configured the column. */}
              <CheckboxField
                inputId="col-allow-create"
                checked={allowCreate}
                onChange={setAllowCreate}
                label="Let people add a new row while filling this in"
                hint="Only for people who can already create rows in the target list; everyone else just sees the search. Typing a name that doesn't exist still fails on import and in the API."
              />
            </>
          )}

          {!multiValued && (
            <CheckboxField
              inputId="col-unique"
              checked={unique}
              onChange={setUnique}
              label="Unique"
              hint={
                editing && !column.config.unique && unique
                  ? "Existing duplicates are left alone — this is checked when a record is saved, so they surface the next time one of them is edited."
                  : isCalculated
                    ? "No two records may work out to the same answer. The record being saved is refused, even though the value came from the formula rather than from anything typed."
                    : "No two records may hold the same value."
              }
            />
          )}
          {(showDefault || supportsOnUpdate) && (
            <div className="flex flex-col gap-3">
              <div className="form-section-head">
                <span className="field-label">Filling in</span>
                <p className="field-hint">
                  What a new record starts with, and what the app keeps up to
                  date on its own.
                </p>
              </div>
              {showDefault && type === "checkbox" && (
                <Field label="Default value" htmlFor="col-default" optional>
                  <Dropdown
                    inputId="col-default"
                    value={defaultChecked}
                    options={[
                      { label: "No default", value: null },
                      { label: "Checked", value: true },
                      { label: "Unchecked", value: false },
                    ]}
                    onChange={(e) => setDefaultChecked(e.value)}
                  />
                </Field>
              )}
              {showDefault && type !== "checkbox" && (
                <Field label="Default value" htmlFor="col-default" optional>
                  {/* The default is edited the way the cell itself will be, so a
                      multiline column's default can actually contain line breaks.
                      A rich column gets the plain box too rather than a second
                      editor: a default is a starting sentence, not a formatted
                      document, and it is sanitised on write like anything else. */}
                  {type === "text" && textMode !== "single" ? (
                    <InputTextarea
                      id="col-default"
                      value={defaultText}
                      onChange={(e) => setDefaultText(e.target.value)}
                      rows={3}
                      placeholder="Used when left blank"
                    />
                  ) : (
                    (type === "text" ||
                      type === "link" ||
                      type === "email" ||
                      type === "phone" ||
                      type === "color") && (
                      <InputText
                        id="col-default"
                        className={type === "color" ? "font-mono" : undefined}
                        value={defaultText}
                        onChange={(e) => setDefaultText(e.target.value)}
                        placeholder={
                          type === "link"
                            ? "https://example.com"
                            : type === "email"
                              ? "name@example.com"
                              : type === "color"
                                ? "#1d4ed8"
                                : "Used when left blank"
                        }
                      />
                    )
                  )}
                  {(type === "number" ||
                    type === "rating" ||
                    type === "duration") && (
                    <InputNumber
                      inputId="col-default"
                      value={defaultNumber}
                      onValueChange={(e) => setDefaultNumber(e.value ?? null)}
                      min={
                        type === "rating" ? 1 : type === "duration" ? 0 : undefined
                      }
                      max={type === "rating" ? (max ?? 5) : undefined}
                      suffix={type === "duration" ? " s" : undefined}
                      useGrouping={false}
                    />
                  )}
                  {type === "choice" &&
                    (multiple ? (
                      // No `editable` counterpart: a MultiSelect takes no free
                      // text, so a `multiple` column's default is one or more of
                      // its own options even when `allow_other` is on. The cells
                      // still take a write-in; only this control does not.
                      <MultiSelect
                        inputId="col-default"
                        value={defaultChoices}
                        options={choiceLabels.map((l) => ({
                          label: l,
                          value: l,
                        }))}
                        onChange={(e) => setDefaultChoices(e.value ?? [])}
                        placeholder="No default"
                        showClear
                        display="chip"
                        className="w-full"
                      />
                    ) : (
                      // Typable when the column takes an answer of its own, because
                      // then a default off the option list is as valid as a cell
                      // holding one — the server judges it by the same rule.
                      <Dropdown
                        inputId="col-default"
                        value={defaultChoice}
                        options={choiceLabels.map((l) => ({
                          label: l,
                          value: l,
                        }))}
                        onChange={(e) => setDefaultChoice(e.value ?? null)}
                        placeholder="No default"
                        showClear
                        editable={allowOther}
                        className="w-full"
                      />
                    ))}
                  {isTemporal(type) && (
                    <div className="flex flex-col gap-2">
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
                        className="w-full"
                      />
                      {defaultTemporal === SPECIFIC && (
                        <Calendar
                          value={defaultDate}
                          onChange={(e) =>
                            setDefaultDate((e.value as Date) ?? null)
                          }
                          dateFormat="yy-mm-dd"
                          timeOnly={type === "time"}
                          showTime={type === "datetime"}
                          showButtonBar={type === "date"}
                        />
                      )}
                    </div>
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
                      className="w-full"
                    />
                  )}
                </Field>
              )}

              {supportsOnUpdate && (
                <Field
                  label="On update"
                  htmlFor="col-onupdate"
                  hint="Filled in automatically when a record is created and re-stamped on every edit; it isn't set by hand."
                >
                  <Dropdown
                    inputId="col-onupdate"
                    value={onUpdate}
                    options={[
                      { label: "Off", value: "" },
                      {
                        label: dynamicLabel(type),
                        value: dynamicToken(type) as string,
                      },
                    ]}
                    onChange={(e) => setOnUpdate(e.value)}
                    className="w-full"
                  />
                </Field>
              )}
            </div>
          )}
        </FormSection>
      </div>
    </AppDialog>
  );
}
