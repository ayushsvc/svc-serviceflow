type ClipboardItemLike = { kind: string; type: string; getAsFile: () => File | null };
type ClipboardDataLike = {
  items?: ArrayLike<ClipboardItemLike> | null;
  getData?: (format: string) => string;
};

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function screenshotName(now: Date, index = 0): string {
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `screenshot-${stamp}${index > 0 ? `-${index + 1}` : ""}.png`;
}

// Returns pasted images as screenshot-YYYYMMDD-HHMMSS.png files, or [] when the
// clipboard has no image or carries text (so normal text pasting is never intercepted).
export function getImageFilesFromClipboard(data: ClipboardDataLike | null | undefined, now: Date = new Date()): File[] {
  if (!data || !data.items) return [];
  if ((data.getData?.("text/plain") ?? "").length > 0) return [];
  const images: File[] = [];
  for (let i = 0; i < data.items.length; i += 1) {
    const item = data.items[i];
    if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
    const file = item.getAsFile();
    if (file) images.push(file);
  }
  return images.map((file, index) => new File([file], screenshotName(now, index), { type: file.type || "image/png" }));
}
