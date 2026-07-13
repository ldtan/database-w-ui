import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { Calendar } from "primereact/calendar";
import { Checkbox } from "primereact/checkbox";
import { Button } from "primereact/button";
import { Divider } from "primereact/divider";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { fmtDate, parseTemporal } from "../../lib/temporal";
import { useToast } from "../../lib/toast";
import type { ApiUser, Person } from "../../lib/types";

interface Props {
  visible: boolean;
  onHide: () => void;
  /** null = create a new user. */
  user: ApiUser | null;
  /** The user's existing person record, if any. */
  person: Person | null;
  /** Editing your own account, which changes how a password change is handled. */
  isSelf: boolean;
}

export function UserDialog({ visible, onHide, user, person, isSelf }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const editing = !!user;

  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [isSuperuser, setIsSuperuser] = useState(false);

  const [hasPerson, setHasPerson] = useState(false);
  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [namePrefix, setNamePrefix] = useState("");
  const [nameSuffix, setNameSuffix] = useState("");
  const [lastName, setLastName] = useState("");
  const [birthDate, setBirthDate] = useState<Date | null>(null);

  // Reseed whenever the dialog opens, so a previous edit never bleeds into the next.
  useEffect(() => {
    if (!visible) return;
    setUsername(user?.username ?? "");
    setEmail(user?.email ?? "");
    setPassword("");
    setCurrentPassword("");
    setIsActive(user?.is_active ?? true);
    setIsSuperuser(user?.is_superuser ?? false);

    setHasPerson(!!person);
    setNamePrefix(person?.name_prefix ?? "");
    setFirstName(person?.first_name ?? "");
    setMiddleName(person?.middle_name ?? "");
    setLastName(person?.last_name ?? "");
    setNameSuffix(person?.name_suffix ?? "");
    setBirthDate(parseTemporal("date", person?.birth_date));
  }, [visible, user, person]);

  const save = useMutation({
    mutationFn: async () => {
      let userUuid = user?.uuid;

      if (!editing) {
        const created = await api.post<ApiUser>("/auth/users/", {
          username: username.trim(),
          email: email.trim(),
          password,
          is_active: isActive,
        });
        userUuid = created.uuid;
        // An account cannot be *born* a superuser -- UserCreate has no such field,
        // so promotion is a separate, explicit step.
        if (isSuperuser) {
          await api.patch(`/auth/users/${userUuid}`, { is_superuser: true });
        }
      } else {
        const body: Record<string, unknown> = {
          username: username.trim(),
          email: email.trim(),
          is_active: isActive,
          is_superuser: isSuperuser,
        };
        if (password) {
          body.password = password;
          // Changing your own password means proving you know it; a superuser
          // resetting someone else's does not.
          if (isSelf) body.current_password = currentPassword;
        }
        await api.patch(`/auth/users/${user!.uuid}`, body);
      }

      const personBody = {
        name_prefix: namePrefix.trim() || null,
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        middle_name: middleName.trim() || null,
        name_suffix: nameSuffix.trim() || null,
        birth_date: birthDate ? fmtDate(birthDate) : null,
      };
      if (hasPerson) {
        if (person) await api.patch(`/people/${person.uuid}`, personBody);
        else await api.post("/people/", { ...personBody, user_uuid: userUuid });
      } else if (person) {
        await api.del(`/people/${person.uuid}`);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      queryClient.invalidateQueries({ queryKey: ["people"] });
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["person", "me"] });
      toast.success(editing ? "User saved" : "User created", username.trim());
      onHide();
    },
    onError: (err) =>
      toast.error(
        editing ? "Could not save user" : "Could not create user",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const personIncomplete = hasPerson && (!firstName.trim() || !lastName.trim());
  const canSave =
    !!username.trim() &&
    !!email.trim() &&
    (editing || !!password) &&
    (!isSelf || !password || !!currentPassword) &&
    !personIncomplete;

  return (
    <Dialog
      header={editing ? `Edit ${user!.username}` : "Add user"}
      visible={visible}
      onHide={onHide}
      style={{ width: 520 }}
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor="u-username" className="text-sm font-medium">
            Username
          </label>
          <InputText
            id="u-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="u-email" className="text-sm font-medium">
            Email
          </label>
          <InputText
            id="u-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="u-password" className="text-sm font-medium">
            Password{" "}
            {editing && (
              <span style={{ color: "var(--text-muted)" }}>
                (leave blank to keep the current one)
              </span>
            )}
          </label>
          <InputText
            id="u-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {isSelf && !!password && (
          <div className="flex flex-col gap-1">
            <label htmlFor="u-current" className="text-sm font-medium">
              Current password
            </label>
            <InputText
              id="u-current"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
            <small style={{ color: "var(--text-muted)" }}>
              Changing your own password requires the current one.
            </small>
          </div>
        )}

        <div className="flex gap-4">
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="u-active"
              checked={isActive}
              onChange={(e) => setIsActive(!!e.checked)}
            />
            <label htmlFor="u-active">Active</label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="u-superuser"
              checked={isSuperuser}
              onChange={(e) => setIsSuperuser(!!e.checked)}
            />
            <label htmlFor="u-superuser">Superuser</label>
          </div>
        </div>
        {isSelf && user?.is_superuser && !isSuperuser && (
          <Message
            severity="warn"
            text="You are removing your own superuser role. This is refused if you are the last one."
          />
        )}

        <Divider />

        <div className="flex items-center gap-2">
          <Checkbox
            inputId="u-person"
            checked={hasPerson}
            onChange={(e) => setHasPerson(!!e.checked)}
          />
          <label htmlFor="u-person" className="font-medium">
            Connect a person record
          </label>
        </div>
        {!hasPerson && person && (
          <Message
            severity="warn"
            text="Saving will delete this user’s existing person record."
          />
        )}

        {hasPerson && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor="p-prefix" className="text-sm font-medium">
                Prefix{" "}
                <span style={{ color: "var(--text-muted)" }}>(optional)</span>
              </label>
              <InputText
                id="p-prefix"
                value={namePrefix}
                onChange={(e) => setNamePrefix(e.target.value)}
                placeholder="Dr, Ms, …"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="p-first" className="text-sm font-medium">
                First name
              </label>
              <InputText
                id="p-first"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="p-middle" className="text-sm font-medium">
                Middle name{" "}
                <span style={{ color: "var(--text-muted)" }}>(optional)</span>
              </label>
              <InputText
                id="p-middle"
                value={middleName}
                onChange={(e) => setMiddleName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="p-last" className="text-sm font-medium">
                Last name
              </label>
              <InputText
                id="p-last"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="p-suffix" className="text-sm font-medium">
                Suffix{" "}
                <span style={{ color: "var(--text-muted)" }}>(optional)</span>
              </label>
              <InputText
                id="p-suffix"
                value={nameSuffix}
                onChange={(e) => setNameSuffix(e.target.value)}
                placeholder="Jr, III, …"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="p-birth" className="text-sm font-medium">
                Birth date{" "}
                <span style={{ color: "var(--text-muted)" }}>(optional)</span>
              </label>
              <Calendar
                inputId="p-birth"
                value={birthDate}
                onChange={(e) => setBirthDate((e.value as Date) ?? null)}
                dateFormat="yy-mm-dd"
                showIcon
                showButtonBar
                maxDate={new Date()}
              />
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={editing ? "Save" : "Create"}
            onClick={() => save.mutate()}
            loading={save.isPending}
            disabled={!canSave}
          />
        </div>
      </div>
    </Dialog>
  );
}
