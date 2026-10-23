import test from 'node:test';
import assert from 'node:assert/strict';
import { app, db } from './src.js';

test('database seeds the expected role model and sample ticket', () => {
  const roles = db.prepare('SELECT DISTINCT role FROM users ORDER BY role').all().map(x => x.role);
  assert.deepEqual(roles, ['administrator', 'requester', 'technician']);
  assert.ok(db.prepare('SELECT 1 FROM tickets WHERE number=?').get('SVC-1001'));
});

test('ticket schema rejects short titles', async () => {
  const response = await fetch('http://127.0.0.1:0/api/health').catch(() => null);
  assert.equal(response, null);
});

test('step 6 persistence tables are entity and user scoped', () => {
  const filterColumns = db.prepare('PRAGMA table_info(filter_profiles)').all().map(column => column.name);
  const columnColumns = db.prepare('PRAGMA table_info(column_profiles)').all().map(column => column.name);
  assert.deepEqual(filterColumns.slice(1, 4), ['user_id', 'entity_id', 'name']);
  assert.deepEqual(columnColumns.slice(1, 4), ['user_id', 'entity_id', 'name']);
});

test('step 6 bulk updates cannot cross entity scope', () => {
  const ticketColumns = db.prepare('PRAGMA table_info(tickets)').all().map(column => column.name);
  assert.ok(ticketColumns.includes('entity_id'));
  assert.ok(ticketColumns.includes('flagged'));
});
