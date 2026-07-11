// Trigger a browser download of a Blob.
export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Trigger a browser download of in-memory text content.
export function downloadFile(filename: string, content: string, mime: string) {
  downloadBlob(filename, new Blob([content], { type: mime }));
}
