export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_FILES_PER_TICKET = 5;
export const FILE_TOO_LARGE_MESSAGE = "File exceeds the 10 MB limit.";
export const TOO_MANY_FILES_MESSAGE = "A ticket can have at most 5 attachments. Remove one before adding another.";
export const ACCEPT = ".pdf,.png,.jpg,.jpeg,.txt,.csv,.log,.docx,.xlsx";

export type AttachmentStatus = "pending" | "uploading" | "done" | "error";

export type AttachmentItem = {
  id: string;
  file: File;
  name: string;
  size: number;
  status: AttachmentStatus;
  progress: number;
  error?: string;
  remoteId?: number;
  previewUrl?: string;
  pasted?: boolean;
};

export type SetItems = (update: (current: AttachmentItem[]) => AttachmentItem[]) => void;

let counter = 0;

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function makeItem(file: File, pasted = false): AttachmentItem {
  counter += 1;
  const isImage = file.type === "image/png" || file.type === "image/jpeg";
  const oversize = file.size > MAX_FILE_BYTES;
  return {
    id: `att-${Date.now()}-${counter}`,
    file,
    name: file.name,
    size: file.size,
    status: oversize ? "error" : "pending",
    progress: 0,
    error: oversize ? FILE_TOO_LARGE_MESSAGE : undefined,
    previewUrl: isImage && !oversize ? URL.createObjectURL(file) : undefined,
    pasted,
  };
}

export function revokePreview(item: AttachmentItem) {
  if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
}

export function unsentCount(items: AttachmentItem[]): number {
  return items.filter((item) => item.status !== "done").length;
}

function errorMessage(xhr: XMLHttpRequest): string {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) return String((body.detail[0] as { msg?: string }).msg ?? "Upload failed.");
  } catch {
    // fall through to the status based message
  }
  if (xhr.status === 413) return FILE_TOO_LARGE_MESSAGE;
  if (xhr.status === 415) return "This file type is not allowed.";
  return "Upload failed. You can retry or remove this file.";
}

function uploadOne(ticketId: number, file: File, headers: Record<string, string>, onProgress: (percent: number) => void, extra: Record<string, string>): Promise<number> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1/tickets/${ticketId}/documents`);
    Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve((JSON.parse(xhr.responseText) as { id: number }).id);
        } catch {
          resolve(0);
        }
      } else reject(new Error(errorMessage(xhr)));
    };
    xhr.onerror = () => reject(new Error("Network error. You can retry or remove this file."));
    const form = new FormData();
    form.append("document", file);
    Object.entries(extra).forEach(([key, value]) => form.append(key, value));
    xhr.send(form);
  });
}

// Uploads the given items one at a time; a failure marks only that item and never blocks the rest.
// `extra` carries the source link (for example { followup_id: "12" }) for replies and notes.
export async function uploadItems(ticketId: number, ids: string[], items: AttachmentItem[], setItems: SetItems, headers: Record<string, string>, extra: Record<string, string> = {}): Promise<number> {
  let failed = 0;
  const patch = (id: string, change: Partial<AttachmentItem>) =>
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...change } : item)));
  for (const id of ids) {
    const item = items.find((candidate) => candidate.id === id);
    if (!item || item.status === "done") continue;
    patch(id, { status: "uploading", progress: 0, error: undefined });
    try {
      const remoteId = await uploadOne(ticketId, item.file, headers, (progress) => patch(id, { progress }), extra);
      patch(id, { status: "done", progress: 100, remoteId });
    } catch (error) {
      failed += 1;
      patch(id, { status: "error", error: error instanceof Error ? error.message : "Upload failed." });
    }
  }
  return failed;
}

export async function removeRemote(ticketId: number, remoteId: number, headers: Record<string, string>): Promise<boolean> {
  const response = await fetch(`/api/v1/tickets/${ticketId}/documents/${remoteId}`, { method: "DELETE", headers });
  return response.ok;
}
