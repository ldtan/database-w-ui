import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { InputNumber } from "primereact/inputnumber";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";

import { AppDialog } from "../../components/AppDialog";
import { Field, FieldRow, FormSection } from "../../components/Field";
import { NameFields } from "../../components/NameFields";
import { api } from "../../lib/api";
import { isValidName } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { Column, Metric } from "../../lib/types";
import { metricsKey } from "./metricKeys";
import { ErrorMessage } from "../../components/ErrorMessage";

interface Props {
  listUuid: string;
  /** Null when adding. */
  metric: Metric | null;
  visible: boolean;
  onHide: () => void;
}

/** What a metric may aggregate — the backend refuses the rest, so offering them
 *  here would only be a way to get a 400. `calculated` is included on purpose: a
 *  metric is a leaf, so totalling a per-record formula has no cycle to worry
 *  about the way a calculated column referencing another would. `rating` and
 *  `duration` are here because a cell in either is a plain number that aggregates
 *  exactly as `number` does — "total hours" and "average score" are the two things
 *  people open this dialog to ask. Mirrors `REFERENCEABLE_TYPES` on the server. */
const AGGREGATABLE = ["number", "rating", "duration", "text", "calculated"];

export function MetricDialog({ listUuid, metric, visible, onHide }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const editing = metric !== null;

  const [displayName, setDisplayName] = useState("");
  const [name, setName] = useState("");
  const [formula, setFormula] = useState("");
  const [description, setDescription] = useState("");
  const [precision, setPrecision] = useState<number | null>(null);
  const [prefix, setPrefix] = useState("");
  const [suffix, setSuffix] = useState("");

  useEffect(() => {
    if (!visible) return;
    setDisplayName(metric?.display_name ?? "");
    setName(metric?.name ?? "");
    setFormula(metric?.config.formula ?? "");
    setDescription(metric?.description ?? "");
    setPrecision(metric?.config.precision ?? null);
    setPrefix(metric?.config.prefix ?? "");
    setSuffix(metric?.config.suffix ?? "");
  }, [visible, metric]);

  const columns = useQuery({
    queryKey: ["columns", listUuid],
    queryFn: () => api.get<Column[]>(`/lists/${listUuid}/columns/`),
    enabled: visible,
  });
  // Formulas name columns by their slug, not their label — so this is the list
  // someone actually has to type, and it is worth showing rather than guessing at.
  const referenceable = (columns.data ?? [])
    .filter((c) => AGGREGATABLE.includes(c.type))
    .map((c) => c.name);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name,
        display_name: displayName,
        description: description.trim() || null,
        config: {
          formula: formula.trim(),
          // Sent even when cleared: a config PATCH merges onto the stored one, so
          // omitting a field would preserve the old value rather than clear it.
          precision: precision ?? null,
          prefix: prefix.trim() || null,
          suffix: suffix.trim() || null,
        },
      };
      return editing
        ? api.patch<Metric>(`/lists/${listUuid}/metrics/${metric.uuid}`, body)
        : api.post<Metric>(`/lists/${listUuid}/metrics/`, body);
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: metricsKey(listUuid) });
      // The values live under the items key, so this refreshes the cards too.
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      toast.success(editing ? "Metric updated" : "Metric added", saved.display_name);
      onHide();
    },
  });

  return (
    <AppDialog
      header={editing ? `Edit “${metric.display_name}”` : "Add metric"}
      visible={visible}
      onHide={onHide}
      style={{ width: 540 }}
      footer={
        <>
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={editing ? "Save" : "Add metric"}
            icon={editing ? "pi pi-check" : "pi pi-plus"}
            onClick={() => save.mutate()}
            loading={save.isPending}
            disabled={!displayName.trim() || !isValidName(name) || !formula.trim()}
          />
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <ErrorMessage error={save.error} />

        <FormSection title="Basics">
          <NameFields
            key={`${metric?.uuid ?? "new"}:${visible}`}
            idPrefix="sum"
            value={{ displayName, name }}
            onChange={(next) => {
              setDisplayName(next.displayName);
              setName(next.name);
            }}
            linked={!editing}
            autoFocus
          />
          <Field
            label="Formula"
            htmlFor="sum-formula"
            hint={
              <>
                One value over every record the view is showing. Aggregates:{" "}
                <code>SUM</code>, <code>AVG</code>, <code>COUNT</code>,{" "}
                <code>MIN</code>, <code>MAX</code>, <code>MEDIAN</code>,{" "}
                <code>UNIQUE</code>, <code>EMPTY</code>. What goes inside one is an
                ordinary per-record expression, so <code>SUM(qty * price)</code>{" "}
                multiplies first and totals after. Results combine with{" "}
                <code>+ - * /</code> — <code>SUM(revenue) - SUM(cost)</code>.{" "}
                <code>DEFAULT(x, fallback)</code> puts a number on a card that would
                come out blank: <code>DEFAULT(AVG(cost), 0)</code>.
                {referenceable.length > 0
                  ? ` Columns you can aggregate: ${referenceable.join(", ")}.`
                  : " Add a number or text column first — a metric aggregates those."}
              </>
            }
          >
            <InputTextarea
              id="sum-formula"
              value={formula}
              onChange={(e) => setFormula(e.target.value)}
              rows={2}
              placeholder="SUM(hours)"
              style={{ fontFamily: "monospace" }}
            />
          </Field>
          <Field label="Description" htmlFor="sum-description" optional>
            <InputText
              id="sum-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this number means"
            />
          </Field>
        </FormSection>

        <FormSection
          title="Display"
          description="How the number reads on the card. None of this changes what is counted."
        >
          <Field
            label="Decimal places"
            htmlFor="sum-precision"
            optional
            hint="Leave empty to show the value as computed — which is what a count wants."
          >
            <InputNumber
              inputId="sum-precision"
              value={precision}
              onValueChange={(e) => setPrecision(e.value ?? null)}
              min={0}
              max={10}
              showButtons
            />
          </Field>
          <FieldRow>
            <Field label="Prefix" htmlFor="sum-prefix" optional>
              <InputText
                id="sum-prefix"
                value={prefix}
                onChange={(e) => setPrefix(e.target.value)}
                placeholder="$"
              />
            </Field>
            <Field label="Suffix" htmlFor="sum-suffix" optional>
              <InputText
                id="sum-suffix"
                value={suffix}
                onChange={(e) => setSuffix(e.target.value)}
                placeholder="h"
              />
            </Field>
          </FieldRow>
        </FormSection>
      </div>
    </AppDialog>
  );
}
