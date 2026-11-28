// The database, the error type and the small readers every part of the reference shares.
import { DatabaseSync } from "node:sqlite";

export type Db = DatabaseSync;
// deno-lint-ignore no-explicit-any
export type Row = Record<string, any>;

export const HOUR = 3600000;
export const DAY = 86400000;
export const BLOCKING = "('confirmed', 'checked_in')";

export class Fail extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS rooms (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, capacity INTEGER NOT NULL,
  hourly_rate REAL NOT NULL DEFAULT 0, opens TEXT, closes TEXT, min_minutes INTEGER, max_minutes INTEGER,
  buffer_minutes INTEGER NOT NULL DEFAULT 0, equipment TEXT NOT NULL DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL,
  max_active INTEGER, active INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL, customer_id INTEGER NOT NULL,
  starts INTEGER NOT NULL, ends INTEGER NOT NULL, status TEXT NOT NULL, attendees INTEGER NOT NULL, price REAL NOT NULL,
  discount REAL NOT NULL, coupon TEXT, title TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', series_id INTEGER,
  from_waitlist INTEGER NOT NULL DEFAULT 0, idem_key TEXT);
CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL, amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS closures (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL, date TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS coupons (code TEXT PRIMARY KEY, percent_off REAL NOT NULL, max_uses INTEGER);
CREATE TABLE IF NOT EXISTS waitlist (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL, customer_id INTEGER NOT NULL,
  starts INTEGER NOT NULL, ends INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS series (id INTEGER PRIMARY KEY AUTOINCREMENT);
`;

export function open(file: string): Db {
  const db = new DatabaseSync(file);
  db.exec(SCHEMA);
  return db;
}

type Value = string | number | null;

export function one(db: Db, sql: string, ...values: Value[]): Row | undefined {
  return db.prepare(sql).get(...values) as Row | undefined;
}

export function all(db: Db, sql: string, ...values: Value[]): Row[] {
  return db.prepare(sql).all(...values) as Row[];
}

export function run(db: Db, sql: string, ...values: Value[]): number {
  return Number(db.prepare(sql).run(...values).lastInsertRowid);
}

export function round2(amount: number): number {
  return Math.round(amount * 100) / 100;
}

export function iso(instant: number): string {
  return new Date(instant).toISOString();
}

export function time(value: unknown): number {
  const instant = typeof value === "string" ? Date.parse(value) : NaN;
  if (Number.isNaN(instant)) throw new Fail(400, "a valid ISO time is required");
  return instant;
}

export function day(value: unknown): number {
  const shaped = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  const instant = shaped ? Date.parse(value + "T00:00:00Z") : NaN;
  if (Number.isNaN(instant) || iso(instant).slice(0, 10) !== value) throw new Fail(400, "a date like 2031-03-04 is required");
  return instant;
}

export function id(value: unknown): number {
  const number = Number(value);
  return Number.isInteger(number) ? number : -1;
}

export function whole(value: unknown, least: number, what: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < least) throw new Fail(400, what + " must be a whole number of at least " + least);
  return value;
}

export function wholeOrNull(value: unknown, what: string): number | null {
  return value === null || value === undefined ? null : whole(value, 1, what);
}

export function text(value: unknown, what: string): string {
  if (typeof value !== "string" || value.trim() === "") throw new Fail(400, what + " must not be empty");
  return value;
}

export function given(body: Row, key: string, otherwise: unknown): unknown {
  return key in body ? body[key] : otherwise;
}
