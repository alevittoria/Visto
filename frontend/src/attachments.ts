/** Reference files attached to a run: upload, staging for reruns and the many-pages notice. */
import { apiRequest } from './console.ts';
import type { AttachmentPage, Fetcher, Finding, Run } from './console.ts';
import type { CreateRunRequest } from './form.ts';

export interface StagedAttachment { upload_id: string; name: string; page_count: number }

// Mirrors the service: it detects the type from the bytes and stays authoritative.
export const ACCEPTED = ['image/png', 'image/jpeg', 'application/pdf'];
export const MANY_PAGES = 10;
export const MAX_PAGES = 40;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

export function parseStaged(value: unknown): StagedAttachment | null {
  if (!isObject(value) || typeof value.upload_id !== 'string' || !/^[0-9a-f]{32}$/.test(value.upload_id)) return null;
  if (typeof value.name !== 'string' || !value.name.trim()) return null;
  if (!Number.isInteger(value.page_count) || (value.page_count as number) < 1) return null;
  return { upload_id: value.upload_id, name: value.name, page_count: value.page_count as number };
}

function parsed(value: unknown): StagedAttachment {
  const staged = parseStaged(value);
  if (!staged) throw new Error('Invalid attachment response.');
  return staged;
}

export function totalPages(list: readonly StagedAttachment[]): number {
  return list.reduce((sum, item) => sum + item.page_count, 0);
}

/** One notice line for the start screen, or null when the attachments need no comment. */
export function attachmentNotice(list: readonly StagedAttachment[]): string | null {
  const pages = totalPages(list);
  if (pages > MAX_PAGES) return `A run can have at most ${MAX_PAGES} pages of attachments; remove some files.`;
  if (pages <= MANY_PAGES) return null;
  const files = list.length === 1 ? '1 attachment' : `${list.length} attachments`;
  return `${files} (${pages} pages): with this many the comparison may be less accurate. Consider a more capable model in Options.`;
}

export async function uploadAttachment(file: File, fetcher?: Fetcher): Promise<StagedAttachment> {
  const path = `/attachments?name=${encodeURIComponent(file.name)}`;
  return parsed(await apiRequest<unknown>(path, { method: 'POST', file, timeoutMs: 120000 }, fetcher));
}

export async function discardAttachment(uploadId: string, fetcher?: Fetcher): Promise<void> {
  await apiRequest<unknown>(`/attachments/${encodeURIComponent(uploadId)}`, { method: 'DELETE' }, fetcher);
}

/** Stages a stored run's files again so a rerun can attach them. */
export async function stageFromRun(runId: string, fetcher?: Fetcher): Promise<StagedAttachment[]> {
  const value = await apiRequest<unknown>(`/runs/${encodeURIComponent(runId)}/attachments/stage`, { method: 'POST', timeoutMs: 60000 }, fetcher);
  if (!Array.isArray(value)) throw new Error('Invalid attachment response.');
  return value.map(parsed);
}

export function runAttachments(run: Pick<Run, 'goal'>): AttachmentPage[] {
  return Array.isArray(run.goal?.attachments) ? run.goal.attachments : [];
}

export function withAttachments(request: CreateRunRequest, staged: readonly StagedAttachment[]): CreateRunRequest {
  return { ...request, attachments: staged.map((item) => item.upload_id) };
}

/** Appended to the verdict title, e.g. "Passed · 2 findings"; empty when there are none. */
export function findingsSuffix(findings: readonly Finding[] | undefined): string {
  const count = findings?.length ?? 0;
  if (count === 0) return '';
  return count === 1 ? ' · 1 finding' : ` · ${count} findings`;
}

export interface AttachmentLink { id: string; label: string; url: string }

/** The attachment pages a finding cites that this run still lists, with readable labels. */
export function findingEvidence(finding: Finding, pages: readonly AttachmentPage[]): AttachmentLink[] {
  return finding.attachment_ids.flatMap((id) => {
    const page = pages.find((item) => item.attachment_id === id);
    if (!page) return [];
    const label = page.page_count > 1 ? `${page.name}, page ${page.page}` : page.name;
    return [{ id, label, url: page.url }];
  });
}
