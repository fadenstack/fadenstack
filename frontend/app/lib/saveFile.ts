/** Hand a file the page fetched to the browser to save, under `filename`. */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // After the click has started the download: revoking at once can cancel it in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
