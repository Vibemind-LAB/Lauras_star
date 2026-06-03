export interface DropInput {
  filePaths: string[];
  directoryPaths: string[];
  uriText: string;
}

export type DropItem =
  | { kind: "file"; path: string }
  | { kind: "folder"; path: string }
  | { kind: "url"; url: string };

const URL_RE = /^(https?:|ftp:|ftps:|sftp:|magnet:)/i;

export function classifyDrop(input: DropInput): DropItem[] {
  if (input.filePaths.length > 0 || input.directoryPaths.length > 0) {
    return [
      ...input.filePaths.map((path) => ({ kind: "file" as const, path })),
      ...input.directoryPaths.map((path) => ({ kind: "folder" as const, path })),
    ];
  }
  const text = input.uriText.trim().split(/\r?\n/)[0]?.trim() ?? "";
  if (text && URL_RE.test(text)) {
    return [{ kind: "url", url: text }];
  }
  return [];
}
