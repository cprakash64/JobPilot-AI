/** Attach a generated File to a file input via a synthetic DataTransfer, then
 * dispatch change so the employer form registers the upload. Never uploads into
 * the wrong field — the caller passes the field already classified as a
 * resume/cover-letter upload. */

export type UploadOutcome = { status: "uploaded" | "error"; reason?: string; filename?: string };

export function uploadFileToInput(input: HTMLInputElement, file: File): UploadOutcome {
  try {
    const accept = (input.getAttribute("accept") || "").toLowerCase();
    if (accept && !acceptsFile(accept, file)) {
      return { status: "error", reason: `This field only accepts: ${accept}` };
    }
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    const ok = input.files.length === 1 && input.files[0].name === file.name;
    return ok ? { status: "uploaded", filename: file.name } : { status: "error", reason: "upload not confirmed" };
  } catch (err) {
    return { status: "error", reason: err instanceof Error ? err.message : "upload failed" };
  }
}

function acceptsFile(accept: string, file: File): boolean {
  const parts = accept.split(",").map((p) => p.trim());
  const name = file.name.toLowerCase();
  return parts.some((p) => {
    if (p.startsWith(".")) return name.endsWith(p);
    if (p.endsWith("/*")) return file.type.startsWith(p.slice(0, -1));
    return file.type === p;
  });
}
