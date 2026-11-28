// Bookings: making, changing and ending them, with the money, the coupons, the waiting list and the history.
import { all, BLOCKING, Fail, given, HOUR, id, iso, one, round2, run, time, whole } from "./core.ts";
import type { Db, Row } from "./core.ts";
import { findCustomer, mayBook } from "./customers.ts";
import { findRoom, fits, place } from "./rooms.ts";
import type { Slot } from "./rooms.ts";

function paid(db: Db, bookingId: number): number {
  return round2(one(db, "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE booking_id = ?", bookingId)?.total ?? 0);
}

export function bookingJson(db: Db, booking: Row): Row {
  const total = paid(db, booking.id);
  return {
    id: booking.id, roomId: booking.room_id, customerId: booking.customer_id, start: iso(booking.starts), end: iso(booking.ends),
    status: booking.status, attendees: booking.attendees, price: booking.price, couponCode: booking.coupon, discount: booking.discount,
    paid: total, balance: booking.status === "cancelled" ? 0 : round2(booking.price - total), title: booking.title, notes: booking.notes,
    seriesId: booking.series_id, fromWaitlist: booking.from_waitlist === 1,
  };
}

export function findBooking(db: Db, bookingId: unknown): Row {
  const booking = one(db, "SELECT * FROM bookings WHERE id = ?", id(bookingId));
  if (!booking) throw new Fail(404, "no such booking");
  return booking;
}

function note(db: Db, bookingId: number, action: string): void {
  run(db, "INSERT INTO history (booking_id, action, at) VALUES (?, ?, ?)", bookingId, action, new Date().toISOString());
}

export function history(db: Db, bookingId: unknown): Row[] {
  return all(db, "SELECT action, at FROM history WHERE booking_id = ? ORDER BY id", findBooking(db, bookingId).id);
}

// --- coupons

export function createCoupon(db: Db, body: Row): Row {
  const code = typeof body.code === "string" ? body.code.trim() : "";
  const off = body.percentOff;
  if (!code || typeof off !== "number" || !(off >= 1 && off <= 100)) throw new Fail(400, "a code and a percentOff from 1 to 100 are required");
  const most = body.maxUses === undefined || body.maxUses === null ? null : whole(body.maxUses, 1, "maxUses");
  if (one(db, "SELECT code FROM coupons WHERE code = ?", code)) throw new Fail(409, "that code exists");
  run(db, "INSERT INTO coupons (code, percent_off, max_uses) VALUES (?, ?, ?)", code, off, most);
  return { code, percentOff: off, maxUses: most };
}

function uses(db: Db, code: string): number {
  return one(db, `SELECT COUNT(*) AS n FROM bookings WHERE coupon = ? AND status IN ${BLOCKING}`, code)?.n ?? 0;
}

export function couponJson(db: Db, code: string): Row {
  const coupon = one(db, "SELECT * FROM coupons WHERE code = ?", code);
  if (!coupon) throw new Fail(404, "no such coupon");
  return { code: coupon.code, percentOff: coupon.percent_off, maxUses: coupon.max_uses, uses: uses(db, coupon.code) };
}

function usable(db: Db, code: unknown): string | null {
  if (code === undefined || code === null) return null;
  const coupon = one(db, "SELECT * FROM coupons WHERE code = ?", String(code));
  if (!coupon) throw new Fail(400, "no such coupon");
  if (coupon.max_uses !== null && uses(db, coupon.code) >= coupon.max_uses) throw new Fail(409, "the coupon is used up");
  return coupon.code;
}

function priced(db: Db, room: Row, slot: Slot, code: string | null): { price: number; discount: number } {
  const full = round2(((slot.end - slot.start) / HOUR) * room.hourly_rate);
  const off = code ? one(db, "SELECT percent_off FROM coupons WHERE code = ?", code)?.percent_off ?? 0 : 0;
  const discount = round2((full * off) / 100);
  return { price: round2(full - discount), discount };
}

// --- making a booking

function attendees(value: unknown): number {
  return value === undefined ? 1 : whole(value, 1, "attendees");
}

function title(value: unknown): string {
  if (typeof value !== "string" || value.length > 100) throw new Fail(400, "a title is text of at most 100 characters");
  return value;
}

type Origin = { seriesId?: number; fromWaitlist?: boolean; key?: string | null };

function insert(db: Db, slot: Slot, customerId: number, body: Row, origin: Origin): Row {
  const room = place(db, slot);
  const code = usable(db, body.couponCode);
  const money = priced(db, room, slot, code);
  const bookingId = run(db,
    "INSERT INTO bookings (room_id, customer_id, starts, ends, status, attendees, price, discount, coupon, title, notes, series_id, from_waitlist, idem_key) " +
    "VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    room.id, customerId, slot.start, slot.end, slot.attendees, money.price, money.discount, code, title(body.title ?? ""),
    String(body.notes ?? ""), origin.seriesId ?? null, origin.fromWaitlist ? 1 : 0, origin.key ?? null);
  note(db, bookingId, "created");
  return findBooking(db, bookingId);
}

function slotOf(body: Row): Slot {
  return { roomId: id(body.roomId), start: time(body.start), end: time(body.end), attendees: attendees(body.attendees) };
}

export function createBooking(db: Db, body: Row, origin: Origin = {}): Row {
  const slot = slotOf(body);
  if (!(slot.end > slot.start)) throw new Fail(400, "the end must be after the start");
  findRoom(db, slot.roomId);
  const customer = mayBook(db, body.customerId);
  return insert(db, slot, customer.id, body, origin);
}

export function remembered(db: Db, key: string | undefined): Row | undefined {
  return key ? one(db, "SELECT * FROM bookings WHERE idem_key = ?", key) : undefined;
}

export function createSeries(db: Db, body: Row): Row {
  const step = { daily: 24 * HOUR, weekly: 7 * 24 * HOUR }[String(body.repeat)];
  if (!step || typeof body.count !== "number" || !Number.isInteger(body.count) || body.count < 2 || body.count > 52) {
    throw new Fail(400, "repeat is daily or weekly and count is from 2 to 52");
  }
  const first = slotOf(body);
  db.exec("BEGIN");
  try {
    const seriesId = run(db, "INSERT INTO series DEFAULT VALUES");
    const made: Row[] = [];
    for (let index = 0; index < body.count; index++) {
      const shifted = { ...body, start: iso(first.start + index * step), end: iso(first.end + index * step) };
      made.push(createBooking(db, shifted, { seriesId }));
    }
    db.exec("COMMIT");
    return { seriesId, bookings: made.map((booking) => bookingJson(db, booking)) };
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }
}

// --- changing a booking

function confirmed(booking: Row): Row {
  if (booking.status !== "confirmed") throw new Fail(409, "the booking is " + booking.status);
  return booking;
}

export function reschedule(db: Db, bookingId: unknown, body: Row): Row {
  const booking = confirmed(findBooking(db, bookingId));
  const slot: Slot = {
    roomId: booking.room_id, start: time(given(body, "start", iso(booking.starts))), end: time(given(body, "end", iso(booking.ends))),
    attendees: attendees(given(body, "attendees", booking.attendees)),
  };
  const room = place(db, slot, booking.id);
  const money = priced(db, room, slot, booking.coupon);
  run(db, "UPDATE bookings SET starts = ?, ends = ?, attendees = ?, price = ?, discount = ?, title = ?, notes = ? WHERE id = ?",
    slot.start, slot.end, slot.attendees, money.price, money.discount, title(given(body, "title", booking.title)),
    String(given(body, "notes", booking.notes)), booking.id);
  if (slot.start !== booking.starts || slot.end !== booking.ends) note(db, booking.id, "rescheduled");
  return findBooking(db, booking.id);
}

export function move(db: Db, bookingId: unknown, body: Row): Row {
  const found = findBooking(db, bookingId);
  const slot: Slot = { roomId: findRoom(db, body.roomId).id, start: found.starts, end: found.ends, attendees: found.attendees };
  const booking = confirmed(found);
  const room = place(db, slot, booking.id);
  const money = priced(db, room, slot, booking.coupon);
  run(db, "UPDATE bookings SET room_id = ?, price = ?, discount = ? WHERE id = ?", room.id, money.price, money.discount, booking.id);
  return findBooking(db, booking.id);
}

export function mark(db: Db, bookingId: unknown, status: string): Row {
  const booking = confirmed(findBooking(db, bookingId));
  run(db, "UPDATE bookings SET status = ? WHERE id = ?", status, booking.id);
  note(db, booking.id, status);
  return findBooking(db, booking.id);
}

// --- ending a booking, and what follows from it

function promote(db: Db, roomId: number): void {
  for (const entry of all(db, "SELECT * FROM waitlist WHERE room_id = ? ORDER BY id", roomId)) {
    const body = { roomId, customerId: entry.customer_id, start: iso(entry.starts), end: iso(entry.ends) };
    try {
      createBooking(db, body, { fromWaitlist: true });
      run(db, "DELETE FROM waitlist WHERE id = ?", entry.id);
    } catch (error) {
      if (!(error instanceof Fail)) throw error;
    }
  }
}

// Cancels a confirmed booking: the payments go back, the waiting list is tried. Returns what was refunded.
export function cancel(db: Db, bookingId: unknown): number {
  const booking = confirmed(findBooking(db, bookingId));
  const refunded = paid(db, booking.id);
  run(db, "UPDATE bookings SET status = 'cancelled' WHERE id = ?", booking.id);
  if (refunded > 0) run(db, "INSERT INTO payments (booking_id, amount) VALUES (?, ?)", booking.id, -refunded);
  note(db, booking.id, "cancelled");
  promote(db, booking.room_id);
  return refunded;
}

export function cancelSeries(db: Db, seriesId: unknown): number {
  const members = all(db, "SELECT id, status FROM bookings WHERE series_id = ?", id(seriesId));
  if (!members.length) throw new Fail(404, "no such series");
  const open = members.filter((booking) => booking.status === "confirmed");
  open.forEach((booking) => cancel(db, booking.id));
  return open.length;
}

export function noShow(db: Db, bookingId: unknown): Row {
  const booking = mark(db, bookingId, "no_show");
  promote(db, booking.room_id);
  return booking;
}

export function pay(db: Db, bookingId: unknown, body: Row): Row {
  const booking = findBooking(db, bookingId);
  if (!["confirmed", "checked_in"].includes(booking.status)) throw new Fail(409, "the booking is " + booking.status);
  const owed = round2(booking.price - paid(db, booking.id));
  if (typeof body.amount !== "number" || !(body.amount > 0) || body.amount > owed + 0.001) throw new Fail(400, "an amount above 0 and within the balance is required");
  const paymentId = run(db, "INSERT INTO payments (booking_id, amount) VALUES (?, ?)", booking.id, body.amount);
  note(db, booking.id, "paid");
  return { id: paymentId, bookingId: booking.id, amount: body.amount };
}

export function payments(db: Db, bookingId: unknown): Row[] {
  const booking = findBooking(db, bookingId);
  return all(db, "SELECT id, booking_id AS bookingId, amount FROM payments WHERE booking_id = ? ORDER BY id", booking.id);
}

export function close(db: Db, roomId: unknown, body: Row, midnight: number): Row {
  const room = findRoom(db, roomId);
  const date = iso(midnight).slice(0, 10);
  const hit = all(db, "SELECT id FROM bookings WHERE room_id = ? AND status = 'confirmed' AND starts < ? AND ends > ? ORDER BY id", room.id, midnight + 24 * HOUR, midnight);
  if (hit.length && body.cancelBookings !== true) throw new Fail(409, "the room has bookings that day");
  const closureId = run(db, "INSERT INTO closures (room_id, date, reason) VALUES (?, ?, ?)", room.id, date, body.reason === undefined ? null : String(body.reason));
  hit.forEach((booking) => cancel(db, booking.id));
  return { id: closureId, date, reason: body.reason ?? null, cancelled: hit.map((booking) => booking.id) };
}

export function removeCustomer(db: Db, customerId: unknown): void {
  const customer = findCustomer(db, customerId);
  run(db, "DELETE FROM waitlist WHERE customer_id = ?", customer.id);
  run(db, "UPDATE customers SET deleted = 1 WHERE id = ?", customer.id);
  for (const booking of all(db, "SELECT id FROM bookings WHERE customer_id = ? AND status = 'confirmed'", customer.id)) cancel(db, booking.id);
}

// --- the waiting list

export function wait(db: Db, roomId: unknown, body: Row): Row {
  const room = findRoom(db, roomId);
  const customer = mayBook(db, body.customerId, false);
  const slot: Slot = { roomId: room.id, start: time(body.start), end: time(body.end), attendees: 1 };
  if (!(slot.end > slot.start)) throw new Fail(400, "the end must be after the start");
  if (fits(db, slot)) throw new Fail(409, "the room is free then: book it");
  const entryId = run(db, "INSERT INTO waitlist (room_id, customer_id, starts, ends) VALUES (?, ?, ?, ?)", room.id, customer.id, slot.start, slot.end);
  return { id: entryId, roomId: room.id, customerId: customer.id, start: iso(slot.start), end: iso(slot.end) };
}

export function waiting(db: Db, roomId: unknown): Row[] {
  const room = findRoom(db, roomId);
  return all(db, "SELECT * FROM waitlist WHERE room_id = ? ORDER BY id", room.id)
    .map((entry) => ({ id: entry.id, roomId: entry.room_id, customerId: entry.customer_id, start: iso(entry.starts), end: iso(entry.ends) }));
}

// --- lists and reports

type Query = (name: string) => string | undefined;

function count(value: string | undefined, what: string): number | null {
  if (value === undefined) return null;
  if (!/^\d+$/.test(value)) throw new Fail(400, what + " must be a whole number of 0 or more");
  return Number(value);
}

function matching(db: Db, query: Query): Row[] {
  const wanted: [string, string | number][] = [];
  for (const [name, column] of [["roomId", "room_id"], ["customerId", "customer_id"], ["seriesId", "series_id"]]) {
    if (query(name) !== undefined) wanted.push([column + " = ?", id(query(name))]);
  }
  if (query("status") !== undefined) wanted.push(["status = ?", query("status") as string]);
  if (query("to") !== undefined) wanted.push(["starts < ?", time(query("to"))]);
  if (query("from") !== undefined) wanted.push(["ends > ?", time(query("from"))]);
  if (query("q") !== undefined) wanted.push(["LOWER(title) LIKE ?", "%" + (query("q") as string).toLowerCase() + "%"]);
  const where = wanted.length ? " WHERE " + wanted.map(([clause]) => clause).join(" AND ") : "";
  const order = { start: "starts, id", "-start": "starts DESC, id DESC" }[query("sort") ?? ""] ?? "id";
  return all(db, "SELECT * FROM bookings" + where + " ORDER BY " + order, ...wanted.map(([, value]) => value));
}

export function listBookings(db: Db, query: Query): { total: number; page: Row[] } {
  const limit = count(query("limit"), "limit");
  const offset = count(query("offset"), "offset") ?? 0;
  const rows = matching(db, query);
  return { total: rows.length, page: rows.slice(offset, limit === null ? undefined : offset + limit).map((row) => bookingJson(db, row)) };
}

export function csv(db: Db, query: Query): string {
  const lines = matching(db, query).map((row) => [row.id, row.room_id, row.customer_id, iso(row.starts), iso(row.ends), row.status, row.price].join(","));
  return ["id,roomId,customerId,start,end,status,price", ...lines].join("\n") + "\n";
}

function counted(db: Db, from: number, to: number): Row[] {
  return all(db, `SELECT * FROM bookings WHERE status IN ${BLOCKING} AND starts >= ? AND starts < ? ORDER BY room_id`, from, to);
}

export function daily(db: Db, midnight: number): Row {
  const rows = counted(db, midnight, midnight + 24 * HOUR);
  const rooms = new Map<number, Row>();
  for (const row of rows) {
    const entry = rooms.get(row.room_id) ?? { roomId: row.room_id, bookings: 0, revenue: 0 };
    rooms.set(row.room_id, { roomId: row.room_id, bookings: entry.bookings + 1, revenue: round2(entry.revenue + row.price) });
  }
  const revenue = round2(rows.reduce((sum, row) => sum + row.price, 0));
  return { date: iso(midnight).slice(0, 10), bookings: rows.length, revenue, rooms: [...rooms.values()] };
}

export function utilization(db: Db, from: number, to: number): Row[] {
  if (to < from) throw new Fail(400, "to must not be before from");
  const rows = counted(db, from, to + 24 * HOUR);
  return all(db, "SELECT id FROM rooms WHERE deleted = 0 ORDER BY id").map((room) => {
    const mine = rows.filter((row) => row.room_id === room.id);
    return { roomId: room.id, hours: round2(mine.reduce((sum, row) => sum + (row.ends - row.starts) / HOUR, 0)) };
  });
}
