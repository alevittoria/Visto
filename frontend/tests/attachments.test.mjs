import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ACCEPTED, attachmentNotice, discardAttachment, MANY_PAGES, MAX_PAGES, parseStaged, runAttachments, stageFromRun,
  totalPages, uploadAttachment, withAttachments,
} from '../src/attachments.ts';

const staged = (pages, name = 'fictional-flow.pdf') => ({ upload_id: 'a'.repeat(32), name, page_count: pages });

function fakeFetch(calls, body, status = 200) {
  return async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  };
}

test('accepted types and limits match the service', () => {
  assert.deepEqual(ACCEPTED, ['image/png', 'image/jpeg', 'application/pdf']);
  assert.equal(MANY_PAGES, 10);
  assert.equal(MAX_PAGES, 40);
});

test('the many-attachments notice starts above ten pages', () => {
  assert.equal(attachmentNotice([]), null);
  assert.equal(attachmentNotice([staged(10)]), null);
  assert.equal(totalPages([staged(4), staged(7)]), 11);
  assert.equal(
    attachmentNotice([staged(4), staged(7)]),
    '2 attachments (11 pages): with this many the comparison may be less accurate. Consider a more capable model in Options.',
  );
  assert.match(attachmentNotice([staged(41)]), /at most 40 pages/);
});

test('staged uploads are validated', () => {
  assert.deepEqual(parseStaged(staged(2)), staged(2));
  for (const bad of [null, { ...staged(2), page_count: 0 }, { ...staged(2), upload_id: '../x' }, { ...staged(2), name: '' }]) {
    assert.equal(parseStaged(bad), null);
  }
});

test('upload sends the raw file with its name and the local guard', async () => {
  const calls = [];
  const file = new File([new Uint8Array([137, 80, 78, 71])], 'fictional login.png', { type: 'image/png' });
  assert.deepEqual(await uploadAttachment(file, fakeFetch(calls, staged(1, 'fictional login.png'))), staged(1, 'fictional login.png'));
  assert.equal(calls[0].url, '/api/attachments?name=fictional%20login.png');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['X-Visto'], 'local-console');
  assert.ok(calls[0].init.body instanceof Blob);
  await assert.rejects(uploadAttachment(file, fakeFetch([], { upload_id: 'x' })));
});

test('discard and rerun staging use the attachment routes', async () => {
  const calls = [];
  await discardAttachment('b'.repeat(32), fakeFetch(calls, { discarded: true }));
  assert.deepEqual([calls[0].url, calls[0].init.method], [`/api/attachments/${'b'.repeat(32)}`, 'DELETE']);
  assert.deepEqual(await stageFromRun('run-1', fakeFetch(calls, [staged(2)])), [staged(2)]);
  assert.deepEqual([calls[1].url, calls[1].init.method], ['/api/runs/run-1/attachments/stage', 'POST']);
});

test('a stored run lists its attachments and a request carries upload ids', () => {
  assert.deepEqual(runAttachments({ goal: { text: 'x', expected_result: null, constraints: [] } }), []);
  const page = { attachment_id: 'p1', name: 'fictional-flow.pdf', page: 1, page_count: 1, width: 1, height: 1, media_type: 'image/png', url: '/api/runs/r/attachments/p1' };
  assert.deepEqual(runAttachments({ goal: { text: 'x', expected_result: null, constraints: [], attachments: [page] } }), [page]);
  assert.deepEqual(withAttachments({ goal: 'x', attachments: [] }, [staged(1)]).attachments, ['a'.repeat(32)]);
});

test('findings are counted in the outcome title and paired with their evidence', async () => {
  const { findingsSuffix, findingEvidence } = await import('../src/attachments.ts');
  assert.equal(findingsSuffix(undefined), '');
  assert.equal(findingsSuffix([]), '');
  const finding = { explanation: 'Share action missing', observation_ids: ['o1'], attachment_ids: ['p1', 'gone'] };
  assert.equal(findingsSuffix([finding]), ' · 1 finding');
  assert.equal(findingsSuffix([finding, finding]), ' · 2 findings');
  const page = { attachment_id: 'p1', name: 'fictional-flow.pdf', page: 2, page_count: 3, width: 1, height: 1, media_type: 'image/png', url: '/api/runs/r/attachments/p1' };
  assert.deepEqual(findingEvidence(finding, [page]), [{ id: 'p1', label: 'fictional-flow.pdf, page 2', url: page.url }]);
});
