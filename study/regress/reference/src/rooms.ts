// Rooms, and the rules a room puts on a booking: capacity, length, opening hours, closures, other bookings.
import { all, BLOCKING, DAY, Fail, given, id, iso, one, run, text, whole, wholeOrNull } from "./core.ts";
import type { Db, Row } from "./core.ts";

export type Slot = { roomId: number; start: number; end: number; attendees: number };

const NEW_ROOM: Row = { hourly_rate: 0, min_minutes: null, max_minutes: null, buffer_minutes: 0, equipment: "[]" };

export function roomJson(room: Row): Row {
  return {
    id: room.id, name: room.name, capacity: room.capacity, hourlyRate: room.hourly_rate, minMinutes: room.min_minutes,
    maxMinutes: room.max_minutes, bufferMinutes: room.buffer_minutes, equipment: JSON.parse(room.equipment),
  };
}

export function findRoom(db: Db, roomId: unknown): Row {
  const room = one(db, "SELECT * FROM rooms WHERE id = ? AND deleted = 0", id(roomId));
  if (!room) throw new Fail(404, "no such room");
  return room;
}

function equipment(value: unknown): string {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) throw new Fail(400, "equipment must be an array of text");
  return JSON.stringify(value);
}

function rate(value: unknown): number {
  if (typeof value !== "number" || !(value >= 0)) throw new Fail(400, "hourlyRate must be a number of 0 or more");
  return value;
}

function fields(body: Row, now: Row): Row {
  return {
    name: text(given(body, "name", now.name), "name"),
    capacity: whole(given(body, "capacity", now.capacity), 1, "capacity"),
    hourly_rate: rate(given(body, "hourlyRate", now.hourly_rate)),
    min_minutes: wholeOrNull(given(body, "minMinutes", now.min_minutes), "minMinutes"),
    max_minutes: wholeOrNull(given(body, "maxMinutes", now.max_minutes), "maxMinutes"),
    buffer_minutes: whole(given(body, "bufferMinutes", now.buffer_minutes), 0, "bufferMinutes"),
    equipment: equipment(given(body, "equipment", JSON.parse(now.equipment))),
  };
}

export function createRoom(db: Db, body: Row): Row {
  const f = fields(body, NEW_ROOM);
  const roomId = run(db, "INSERT INTO rooms (name, capacity, hourly_rate, min_minutes, max_minutes, buffer_minutes, equipment) VALUES (?, ?, ?, ?, ?, ?, ?)",
    f.name, f.capacity, f.hourly_rate, f.min_minutes, f.max_minutes, f.buffer_minutes, f.equipment);
  return findRoom(db, roomId);
}

export function updateRoom(db: Db, roomId: unknown, body: Row): Row {
  const f = fields(body, findRoom(db, roomId));
  run(db, "UPDATE rooms SET name = ?, capacity = ?, hourly_rate = ?, min_minutes = ?, max_minutes = ?, buffer_minutes = ?, equipment = ? WHERE id = ?",
    f.name, f.capacity, f.hourly_rate, f.min_minutes, f.max_minutes, f.buffer_minutes, f.equipment, id(roomId));
  return findRoom(db, roomId);
}

export function deleteRoom(db: Db, roomId: unknown): void {
  const room = findRoom(db, roomId);
  if (one(db, `SELECT id FROM bookings WHERE room_id = ? AND status IN ${BLOCKING}`, room.id)) throw new Fail(409, "the room still has bookings");
  run(db, "UPDATE rooms SET deleted = 1 WHERE id = ?", room.id);
}

export function listRooms(db: Db, wanted: string | undefined): Row[] {
  const items = wanted ? wanted.split(",").filter(Boolean) : [];
  return all(db, "SELECT * FROM rooms WHERE deleted = 0 ORDER BY id").filter((room) => {
    const has: string[] = JSON.parse(room.equipment);
    return items.every((item) => has.includes(item));
  });
}

function clock(value: unknown): string {
  if (typeof value !== "string" || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) throw new Fail(400, "a time like 08:30 is required");
  return value;
}

export function setHours(db: Db, roomId: unknown, body: Row): Row {
  const room = findRoom(db, roomId);
  const opens = clock(body.open);
  const closes = clock(body.close);
  if (closes <= opens) throw new Fail(400, "close must be after open");
  run(db, "UPDATE rooms SET opens = ?, closes = ? WHERE id = ?", opens, closes, room.id);
  return { open: opens, close: closes };
}

export function clearHours(db: Db, roomId: unknown): void {
  run(db, "UPDATE rooms SET opens = NULL, closes = NULL WHERE id = ?", findRoom(db, roomId).id);
}

function minutes(clockTime: string): number {
  return Number(clockTime.slice(0, 2)) * 60 + Number(clockTime.slice(3));
}

function withinLength(room: Row, slot: Slot): void {
  const length = (slot.end - slot.start) / 60000;
  if (room.min_minutes !== null && length < room.min_minutes) throw new Fail(400, "shorter than the room allows");
  if (room.max_minutes !== null && length > room.max_minutes) throw new Fail(400, "longer than the room allows");
}

function withinHours(room: Row, slot: Slot): void {
  if (room.opens === null) return;
  const midnight = Math.floor(slot.start / DAY) * DAY;
  const opens = midnight + minutes(room.opens) * 60000;
  const closes = midnight + minutes(room.closes) * 60000;
  if (slot.start < opens || slot.end > closes) throw new Fail(400, "outside the room's opening hours");
}

export function closedDays(db: Db, roomId: number, start: number, end: number): Row[] {
  return all(db, "SELECT * FROM closures WHERE room_id = ?", roomId).filter((closure) => {
    const from = Date.parse(closure.date + "T00:00:00Z");
    return start < from + DAY && end > from;
  });
}

function clash(db: Db, room: Row, slot: Slot, ignore: number): void {
  const gap = room.buffer_minutes * 60000;
  const other = one(db, `SELECT id FROM bookings WHERE room_id = ? AND id != ? AND status IN ${BLOCKING} AND starts < ? AND ends > ?`,
    room.id, ignore, slot.end + gap, slot.start - gap);
  if (other) throw new Fail(409, "the room is already booked then");
}

// The room a booking for this slot would go into, or the error that refuses it. `ignore` is the booking being moved.
export function place(db: Db, slot: Slot, ignore = 0): Row {
  if (!(slot.end > slot.start)) throw new Fail(400, "the end must be after the start");
  const room = findRoom(db, slot.roomId);
  if (slot.attendees > room.capacity) throw new Fail(400, "more attendees than the room holds");
  withinLength(room, slot);
  withinHours(room, slot);
  if (closedDays(db, room.id, slot.start, slot.end).length) throw new Fail(409, "the room is closed that day");
  clash(db, room, slot, ignore);
  return room;
}

export function fits(db: Db, slot: Slot): boolean {
  try {
    place(db, slot);
    return true;
  } catch (error) {
    if (error instanceof Fail) return false;
    throw error;
  }
}

function dayWindow(room: Row, midnight: number): [number, number] {
  if (room.opens === null) return [midnight, midnight + DAY];
  return [midnight + minutes(room.opens) * 60000, midnight + minutes(room.closes) * 60000];
}

export function availability(db: Db, roomId: unknown, midnight: number): Row[] {
  const room = findRoom(db, roomId);
  if (closedDays(db, room.id, midnight, midnight + DAY).length) return [];
  const [from, to] = dayWindow(room, midnight);
  const taken = all(db, `SELECT starts, ends FROM bookings WHERE room_id = ? AND status IN ${BLOCKING} AND starts < ? AND ends > ? ORDER BY starts`, room.id, to, from);
  const free: Row[] = [];
  let cursor = from;
  for (const booking of taken) {
    if (booking.starts > cursor) free.push({ start: iso(cursor), end: iso(booking.starts) });
    cursor = Math.max(cursor, booking.ends);
  }
  if (cursor < to) free.push({ start: iso(cursor), end: iso(to) });
  return free;
}
