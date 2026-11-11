import { useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { ACCEPTED, attachmentNotice, discardAttachment, uploadAttachment } from '../attachments.ts';
import type { StagedAttachment } from '../attachments.ts';
import { ApiError } from '../console.ts';

interface Props {
  attachments: StagedAttachment[];
  onChange: (update: (list: StagedAttachment[]) => StagedAttachment[]) => void;
  /** True while files are being uploaded; the start button waits for them. */
  onBusy: (busy: boolean) => void;
  disabled: boolean;
}

/** Hook for dropping files onto any element of the start screen. */
export function useFileDrop(upload: (files: File[]) => void) {
  return {
    onDragOver: (event: DragEvent) => {
      if (event.dataTransfer.types.includes('Files')) event.preventDefault();
    },
    onDrop: (event: DragEvent) => {
      if (event.dataTransfer.files.length === 0) return;
      event.preventDefault();
      upload([...event.dataTransfer.files]);
    },
  };
}

/** The Attach button with its hidden file picker. */
export function AttachButton({ onPick, disabled }: { onPick: (files: File[]) => void; disabled: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={input} type="file" multiple accept={ACCEPTED.join(',')} className="visually-hidden" tabIndex={-1}
        aria-hidden="true" onChange={(event) => {
          const files = [...(event.target.files ?? [])];
          event.target.value = '';
          if (files.length) onPick(files);
        }} />
      <button type="button" className="chip" disabled={disabled} onClick={() => input.current?.click()}
        title="Attach PNG, JPG or PDF files for the AI to use, for example layouts to compare with">
        <span aria-hidden="true">📎</span> Attach
      </button>
    </>
  );
}

export function useAttachmentUploads({ onChange, onBusy }: Pick<Props, 'onChange' | 'onBusy'>) {
  const [errors, setErrors] = useState<string[]>([]);
  const pending = useRef(0);

  async function upload(files: File[]) {
    setErrors([]);
    pending.current += files.length;
    onBusy(true);
    const failed: string[] = [];
    // One at a time: the service validates and renders each file before the next.
    for (const file of files) {
      try {
        const staged = await uploadAttachment(file);
        onChange((list) => [...list, staged]);
      } catch (error) {
        failed.push(`${file.name}: ${error instanceof ApiError ? error.message : 'the file could not be uploaded.'}`);
      } finally {
        pending.current -= 1;
      }
    }
    if (pending.current === 0) onBusy(false);
    setErrors(failed);
  }

  return { upload, errors };
}

/** The attached-file chips and the many-pages notice. */
export function AttachmentList({ attachments, onChange, disabled }: Omit<Props, 'onBusy'>) {
  if (attachments.length === 0) return null;
  const notice = attachmentNotice(attachments);
  function remove(item: StagedAttachment) {
    onChange((list) => list.filter((entry) => entry.upload_id !== item.upload_id));
    // Best effort: an unclaimed upload also expires on its own.
    void discardAttachment(item.upload_id).catch(() => undefined);
  }
  return (
    <div className="attachments">
      <ul className="attachment-chips" aria-label="Attached files">
        {attachments.map((item) => (
          <li key={item.upload_id} className="attachment-chip">
            <span>{item.name}{item.page_count > 1 ? ` · ${item.page_count} pages` : ''}</span>
            <button type="button" className="link" disabled={disabled} onClick={() => remove(item)}
              aria-label={`Remove ${item.name}`}>✕</button>
          </li>
        ))}
      </ul>
      {notice && <p className="start-warning" role="status">{notice}</p>}
    </div>
  );
}
