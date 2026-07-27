import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { InputText } from "primereact/inputtext";
import { Calendar } from "primereact/calendar";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useCurrentUser } from "../../lib/auth";
import { useMyPerson } from "../../lib/people";
import { fmtDate, parseTemporal } from "../../lib/temporal";
import { useToast } from "../../lib/toast";
import type { Person } from "../../lib/types";
import { PageHeader } from "../../components/PageHeader";
import { UserAvatar } from "../../components/UserAvatar";

/**
 * Your own account and profile.
 *
 * The profile (`Person`) is optional and may not exist yet — `useMyPerson` returns
 * null rather than throwing on the 404, because "no profile" is a normal state for
 * an account, not an error. The form therefore doubles as a create form.
 *
 * Fields are sent with `exclude_unset` semantics on the backend: an omitted field
 * is left alone, an explicit null clears a nullable one. That is why blanking a
 * middle name sends null rather than "".
 */
export function ProfilePage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const me = useCurrentUser();

  // 404 means "no profile yet", which is a normal state, not an error — the rule
  // lives in the hook, so the drawer's copy of this cannot drift from it.
  const person = useMyPerson();

  const [namePrefix, setNamePrefix] = useState("");
  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [lastName, setLastName] = useState("");
  const [nameSuffix, setNameSuffix] = useState("");
  const [birthDate, setBirthDate] = useState<Date | null>(null);

  // Seed the form once the record loads (or clear it after a delete).
  useEffect(() => {
    const data = person.data;
    setNamePrefix(data?.name_prefix ?? "");
    setFirstName(data?.first_name ?? "");
    setMiddleName(data?.middle_name ?? "");
    setLastName(data?.last_name ?? "");
    setNameSuffix(data?.name_suffix ?? "");
    setBirthDate(parseTemporal("date", data?.birth_date));
  }, [person.data]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["person", "me"] });

  const save = useMutation({
    mutationFn: () => {
      const body = {
        // Sending null (not "") clears a field server-side.
        name_prefix: namePrefix.trim() || null,
        first_name: firstName.trim(),
        middle_name: middleName.trim() || null,
        last_name: lastName.trim(),
        name_suffix: nameSuffix.trim() || null,
        birth_date: birthDate ? fmtDate(birthDate) : null,
      };
      return person.data
        ? api.patch<Person>(`/people/${person.data.uuid}`, body)
        : api.post<Person>("/people/", body);
    },
    onSuccess: () => {
      invalidate();
      toast.success("Profile saved");
    },
    onError: (err) =>
      toast.error(
        "Could not save profile",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const remove = useMutation({
    mutationFn: () => api.del(`/people/${person.data!.uuid}`),
    onSuccess: () => {
      invalidate();
      toast.success("Profile deleted");
    },
    onError: (err) =>
      toast.error(
        "Could not delete profile",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (firstName.trim() && lastName.trim()) save.mutate();
  }
  function confirmDelete() {
    confirmDialog({
      message: "Delete your person record? Your account itself is unaffected.",
      header: "Confirm",
      icon: "pi pi-exclamation-triangle",
      accept: () => remove.mutate(),
    });
  }

  const canSave = !!firstName.trim() && !!lastName.trim();

  return (
    <div className="flex flex-col gap-5" style={{ maxWidth: 620 }}>
      <ConfirmDialog />
      <PageHeader
        crumbs={[{ label: "Profile" }]}
        title="Profile"
        subtitle="Your account, and the person record other people see you as."
      />

      <div className="surface-card p-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <UserAvatar
            person={person.data}
            username={me?.username ?? ""}
            size="2.75rem"
          />
          <div className="min-w-0">
            <div className="font-medium truncate">{me?.username ?? "…"}</div>
            <div className="text-sm truncate text-muted">{me?.email}</div>
          </div>
        </div>
        {me?.is_superuser && <span className="badge is-brand">superuser</span>}
      </div>

      <h2 className="text-lg font-semibold">Personal details</h2>
      {!person.isLoading && !person.data && (
        <Message
          severity="info"
          text="You don’t have a person record yet. Fill this in to create one."
        />
      )}

      <form onSubmit={onSubmit} className="surface-card p-4 flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor="name-prefix" className="text-sm font-medium">
            Prefix{" "}
            <span style={{ color: "var(--text-muted)" }}>(optional)</span>
          </label>
          <InputText
            id="name-prefix"
            value={namePrefix}
            onChange={(e) => setNamePrefix(e.target.value)}
            placeholder="Dr, Ms, …"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="first-name" className="text-sm font-medium">
            First name
          </label>
          <InputText
            id="first-name"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="middle-name" className="text-sm font-medium">
            Middle name{" "}
            <span style={{ color: "var(--text-muted)" }}>(optional)</span>
          </label>
          <InputText
            id="middle-name"
            value={middleName}
            onChange={(e) => setMiddleName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="last-name" className="text-sm font-medium">
            Last name
          </label>
          <InputText
            id="last-name"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="name-suffix" className="text-sm font-medium">
            Suffix{" "}
            <span style={{ color: "var(--text-muted)" }}>(optional)</span>
          </label>
          <InputText
            id="name-suffix"
            value={nameSuffix}
            onChange={(e) => setNameSuffix(e.target.value)}
            placeholder="Jr, III, …"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="birth-date" className="text-sm font-medium">
            Birth date{" "}
            <span style={{ color: "var(--text-muted)" }}>(optional)</span>
          </label>
          <Calendar
            inputId="birth-date"
            value={birthDate}
            onChange={(e) => setBirthDate((e.value as Date) ?? null)}
            dateFormat="yy-mm-dd"
            showIcon
            showButtonBar
            // The server rejects a future birth date; don't offer one.
            maxDate={new Date()}
          />
        </div>

        <div className="flex gap-2">
          <Button
            type="submit"
            label={person.data ? "Save" : "Create profile"}
            icon="pi pi-check"
            loading={save.isPending}
            disabled={!canSave}
          />
          {person.data && (
            <Button
              type="button"
              label="Delete profile"
              icon="pi pi-trash"
              severity="danger"
              outlined
              onClick={confirmDelete}
              loading={remove.isPending}
            />
          )}
        </div>
      </form>
    </div>
  );
}
