import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Calendar } from "primereact/calendar";
import { Button } from "primereact/button";
import { Message } from "primereact/message";
import { ConfirmDialog, confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { useCurrentUser } from "../../lib/auth";
import { useToast } from "../../lib/toast";
import type { Person } from "../../lib/types";
import { Breadcrumbs } from "../../components/Breadcrumbs";

// A birth date is a calendar date, not an instant. toISOString() would convert
// through UTC and land on the previous day for anyone west of Greenwich, so
// format and parse in local time.
function toIsoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}
function fromIsoDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function ProfilePage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const me = useCurrentUser();

  // 404 means "no profile yet", which is a normal state, not an error.
  const person = useQuery<Person | null>({
    queryKey: ["person", "me"],
    retry: false,
    queryFn: async () => {
      try {
        return await api.get<Person>("/people/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
  });

  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [lastName, setLastName] = useState("");
  const [birthDate, setBirthDate] = useState<Date | null>(null);

  // Seed the form once the record loads (or clear it after a delete).
  useEffect(() => {
    const data = person.data;
    setFirstName(data?.first_name ?? "");
    setMiddleName(data?.middle_name ?? "");
    setLastName(data?.last_name ?? "");
    setBirthDate(data?.birth_date ? fromIsoDate(data.birth_date) : null);
  }, [person.data]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["person", "me"] });

  const save = useMutation({
    mutationFn: () => {
      const body = {
        first_name: firstName.trim(),
        // Sending null (not "") clears the field server-side.
        middle_name: middleName.trim() || null,
        last_name: lastName.trim(),
        birth_date: birthDate ? toIsoDate(birthDate) : null,
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
    <div className="flex flex-col gap-4" style={{ maxWidth: 620 }}>
      <ConfirmDialog />
      <Breadcrumbs items={[{ label: "Profile" }]} />
      <h1 className="text-xl font-semibold">Profile</h1>

      <Card>
        <div className="flex items-center justify-between">
          <div>
            <div className="font-medium">{me?.username ?? "…"}</div>
            <div className="text-sm" style={{ color: "var(--text-muted)" }}>
              {me?.email}
            </div>
          </div>
          {me?.is_superuser && (
            <span className="text-sm font-medium" style={{ color: "var(--brand)" }}>
              superuser
            </span>
          )}
        </div>
      </Card>

      <h2 className="text-lg font-medium">Personal details</h2>
      {person.isLoading && <p>Loading…</p>}
      {!person.isLoading && !person.data && (
        <Message
          severity="info"
          text="You don’t have a person record yet. Fill this in to create one."
        />
      )}

      <form onSubmit={onSubmit} className="flex flex-col gap-3">
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
