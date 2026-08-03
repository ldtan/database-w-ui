import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { FileUpload, type FileUploadHandlerEvent } from "primereact/fileupload";
import { Message } from "primereact/message";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { IMPORT_MAX_BYTES } from "../../lib/config";
import { toCsv } from "../../lib/csv";
import { downloadFile } from "../../lib/download";
import { useToast } from "../../lib/toast";
import type { BatchUserSummary } from "../../lib/types";
import { credentialsCsv } from "./credentials";

interface Props {
  visible: boolean;
  onHide: () => void;
}

// The twelve columns the server accepts, in the order the template writes them.
// Kept in step with KNOWN_HEADERS in backend/src/auth/users/batch/parsing.py by hand:
// a header this file invents is a 400, which is the loud kind of wrong.
const HEADERS = [
  "username",
  "email",
  "password",
  "is_active",
  "is_superuser",
  "groups",
  "name_prefix",
  "first_name",
  "middle_name",
  "last_name",
  "name_suffix",
  "birth_date",
];

// One filled-in row, because a bare header line does not convey that `groups` is
// semicolon-separated, that it wants each group's API name rather than its label, or
// that `birth_date` wants YYYY-MM-DD.
const EXAMPLE = [
  "alovelace",
  "ada@example.com",
  "",
  "true",
  "false",
  "teachers;year_9",
  "Dr",
  "Ada",
  "King",
  "Lovelace",
  "",
  "1815-12-10",
];

/**
 * Create many accounts from one CSV.
 *
 * The counterpart to `UserDialog` for a roster rather than a person. Same two records
 * behind it — a `User` and an optional `Person` — and the same seam hidden: fill the
 * name columns and the row gets a profile, leave them blank and it does not.
 *
 * Bad rows are skipped and reported rather than sinking the file, matching what list
 * import already does. A bad *file* — wrong columns, too many rows — is refused
 * whole, because nothing in it can be trusted.
 *
 * **Passwords the server generated are in the response and nowhere else.** They are
 * not stored and cannot be asked for again, so the credentials download is the only
 * chance to keep them; the panel says so rather than letting anyone find out later.
 */
export function BatchUserDialog({ visible, onHide }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchUserSummary | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  function handleTemplate() {
    downloadFile("users-template.csv", toCsv(HEADERS, [EXAMPLE]), "text/csv");
  }

  function handleClose() {
    // Deliberately dropped, not cached: the generated passwords in here should not
    // outlive the dialog that warned they were one-time.
    setResult(null);
    setFileError(null);
    onHide();
  }

  async function handleUpload(event: FileUploadHandlerEvent) {
    const file = event.files?.[0];
    event.options.clear();
    if (!file) return;
    setResult(null);
    setFileError(null);
    // The server enforces this too (IMPORT_MAX_BYTES); refusing here saves
    // uploading a file that was never going to be read. Same check, same words,
    // as the record import dialog.
    if (file.size > IMPORT_MAX_BYTES) {
      setFileError(
        `This file is ${(file.size / (1024 * 1024)).toFixed(1)} MB, over the ${
          IMPORT_MAX_BYTES / (1024 * 1024)
        } MB limit. Split it and import each part separately.`,
      );
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const summary = await api.postForm<BatchUserSummary>(
        "/auth/users/import",
        form,
      );
      setResult(summary);
      queryClient.invalidateQueries({ queryKey: ["users"] });
      queryClient.invalidateQueries({ queryKey: ["people"] });
      const skipped = summary.errors.length;
      if (skipped === 0) {
        toast.success(
          "Import complete",
          `${summary.created.length} account(s) created`,
        );
      } else {
        toast.warn(
          "Import finished with errors",
          `${summary.created.length} created, ${skipped} skipped`,
        );
      }
    } catch (err) {
      // A file-level refusal, not a row problem: there is no summary to show.
      setFileError(err instanceof ApiError ? err.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  const credentials = result ? credentialsCsv(result.created) : null;

  return (
    <AppDialog
      header="Import users"
      visible={visible}
      onHide={handleClose}
      style={{ width: 560 }}
      footer={
        <>
          <Button
            label="Download template"
            icon="pi pi-file"
            text
            onClick={handleTemplate}
          />
          <Button label="Close" text onClick={handleClose} />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="field-hint">
          Upload a .csv whose headers are the account and profile fields.{" "}
          <strong>username</strong> and <strong>email</strong> are required; leave{" "}
          <strong>password</strong> blank and one is generated. Fill the name columns
          to give the account a profile, or leave them blank for an account without
          one. <strong>groups</strong> takes the <em>API names</em> of existing groups
          — the slug shown under each group on this page, like{" "}
          <code>year_9</code>, not the label <q>Year 9</q> — separated by semicolons.
          Each row is validated on the server and a bad one is skipped, not fatal. Not
          sure of the shape? Download the template.
        </p>
        <p className="field-hint">
          Up to 1,000 accounts and {IMPORT_MAX_BYTES / (1024 * 1024)} MB per file.
          A bigger file is turned away whole — no accounts are created — so split it
          and send each part.
        </p>

        <FileUpload
          mode="basic"
          customUpload
          auto
          accept=".csv"
          chooseLabel="Choose file"
          uploadHandler={handleUpload}
          disabled={busy}
        />

        {fileError && <Message severity="error" text={fileError} />}

        {result && (
          <Message
            severity={result.errors.length === 0 ? "success" : "warn"}
            text={`Created ${result.created.length} account(s)${
              result.errors.length ? `, ${result.errors.length} skipped` : ""
            }.`}
          />
        )}

        {result && result.superusers_created > 0 && (
          <Message
            severity="warn"
            text={`${result.superusers_created} of these accounts were made superusers.`}
          />
        )}

        {credentials && (
          <div className="flex flex-col gap-2">
            <Message
              severity="warn"
              text="Generated passwords are shown once. Download them now — closing this dialog loses them, and a lost one has to be reset from Edit user."
            />
            <Button
              label="Download credentials"
              icon="pi pi-download"
              onClick={() =>
                downloadFile("users-credentials.csv", credentials, "text/csv")
              }
            />
          </div>
        )}

        {result && result.errors.length > 0 && (
          <ul className="text-sm text-danger m-0 pl-4 max-h-40 overflow-auto">
            {result.errors.map((error, index) => (
              <li key={index}>
                {error.row ? `Row ${error.row}: ` : ""}
                {error.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppDialog>
  );
}
