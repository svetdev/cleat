// Every route of the reference, in one table: the path, and the function that answers it.
import { Hono } from "hono";
import type { Context } from "hono";
import * as bookings from "./bookings.ts";
import { all, day, Fail, id, run, time } from "./core.ts";
import type { Db, Row } from "./core.ts";
import * as customers from "./customers.ts";
import * as rooms from "./rooms.ts";

async function body(c: Context): Promise<Row> {
  const parsed = await c.req.json().catch(() => null);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Fail(400, "a JSON object is required");
  return parsed;
}

function roomRoutes(app: Hono, db: Db): void {
  app.post("/rooms", async (c) => c.json(rooms.roomJson(rooms.createRoom(db, await body(c))), 201));
  app.get("/rooms", (c) => c.json(rooms.listRooms(db, c.req.query("equipment")).map(rooms.roomJson)));
  app.get("/rooms/available", (c) => {
    const slot = { roomId: 0, start: time(c.req.query("start")), end: time(c.req.query("end")), attendees: Number(c.req.query("attendees") ?? 1) };
    if (!(slot.end > slot.start) || !Number.isInteger(slot.attendees) || slot.attendees < 1) throw new Fail(400, "a start, a later end and a count of attendees are required");
    const free = rooms.listRooms(db, c.req.query("equipment")).filter((room) => rooms.fits(db, { ...slot, roomId: room.id }));
    return c.json(free.map(rooms.roomJson));
  });
  app.get("/rooms/:id", (c) => c.json(rooms.roomJson(rooms.findRoom(db, c.req.param("id")))));
  app.patch("/rooms/:id", async (c) => c.json(rooms.roomJson(rooms.updateRoom(db, c.req.param("id"), await body(c)))));
  app.delete("/rooms/:id", (c) => {
    rooms.deleteRoom(db, c.req.param("id"));
    return c.body(null, 204);
  });
  app.get("/rooms/:id/availability", (c) => {
    rooms.findRoom(db, c.req.param("id"));
    const midnight = day(c.req.query("date"));
    return c.json({ date: c.req.query("date"), free: rooms.availability(db, c.req.param("id"), midnight) });
  });
  app.put("/rooms/:id/hours", async (c) => c.json(rooms.setHours(db, c.req.param("id"), await body(c))));
  app.get("/rooms/:id/hours", (c) => {
    const room = rooms.findRoom(db, c.req.param("id"));
    return c.json({ open: room.opens, close: room.closes });
  });
  app.delete("/rooms/:id/hours", (c) => {
    rooms.clearHours(db, c.req.param("id"));
    return c.body(null, 204);
  });
}

function closureRoutes(app: Hono, db: Db): void {
  app.post("/rooms/:id/closures", async (c) => {
    rooms.findRoom(db, c.req.param("id"));
    const sent = await body(c);
    return c.json(bookings.close(db, c.req.param("id"), sent, day(sent.date)), 201);
  });
  app.get("/rooms/:id/closures", (c) => {
    const room = rooms.findRoom(db, c.req.param("id"));
    return c.json(all(db, "SELECT id, date, reason FROM closures WHERE room_id = ? ORDER BY id", room.id));
  });
  app.delete("/rooms/:id/closures/:closureId", (c) => removed(c, db, "closures", c.req.param("closureId")));
  app.post("/rooms/:id/waitlist", async (c) => c.json(bookings.wait(db, c.req.param("id"), await body(c)), 201));
  app.get("/rooms/:id/waitlist", (c) => c.json(bookings.waiting(db, c.req.param("id"))));
  app.delete("/rooms/:id/waitlist/:entryId", (c) => removed(c, db, "waitlist", c.req.param("entryId")));
}

function removed(c: Context, db: Db, table: "closures" | "waitlist", rowId: string): Response {
  const room = rooms.findRoom(db, c.req.param("id"));
  const found = all(db, `SELECT id FROM ${table} WHERE id = ? AND room_id = ?`, id(rowId), room.id);
  if (!found.length) throw new Fail(404, "nothing to remove");
  run(db, `DELETE FROM ${table} WHERE id = ?`, found[0].id);
  return c.body(null, 204);
}

function customerRoutes(app: Hono, db: Db): void {
  const json = customers.customerJson;
  app.post("/customers", async (c) => c.json(json(customers.createCustomer(db, await body(c))), 201));
  app.get("/customers", (c) => c.json(all(db, "SELECT * FROM customers WHERE deleted = 0 ORDER BY id").map(json)));
  app.get("/customers/:id", (c) => c.json(json(customers.findCustomer(db, c.req.param("id")))));
  app.patch("/customers/:id", async (c) => c.json(json(customers.updateCustomer(db, c.req.param("id"), await body(c)))));
  app.delete("/customers/:id", (c) => {
    bookings.removeCustomer(db, c.req.param("id"));
    return c.body(null, 204);
  });
  app.post("/customers/:id/deactivate", (c) => c.json(json(customers.setActive(db, c.req.param("id"), 0))));
  app.post("/customers/:id/activate", (c) => c.json(json(customers.setActive(db, c.req.param("id"), 1))));
  app.get("/customers/:id/summary", (c) => c.json(customers.summary(db, c.req.param("id"))));
  app.get("/customers/:id/bookings", (c) => {
    const customer = customers.findCustomer(db, c.req.param("id"));
    return c.json(all(db, "SELECT * FROM bookings WHERE customer_id = ? ORDER BY id", customer.id).map((row) => bookings.bookingJson(db, row)));
  });
}

function bookingRoutes(app: Hono, db: Db): void {
  const shown = (c: Context, row: Row, status: 200 | 201 = 200) => c.json(bookings.bookingJson(db, row), status);
  app.post("/bookings", async (c) => {
    const key = c.req.header("Idempotency-Key");
    const before = bookings.remembered(db, key);
    return before ? shown(c, before) : shown(c, bookings.createBooking(db, await body(c), { key }), 201);
  });
  app.post("/bookings/recurring", async (c) => c.json(bookings.createSeries(db, await body(c)), 201));
  app.get("/bookings", (c) => {
    const found = bookings.listBookings(db, (name) => c.req.query(name));
    c.header("X-Total-Count", String(found.total));
    return c.json(found.page);
  });
  app.get("/bookings.csv", (c) => c.body(bookings.csv(db, (name) => c.req.query(name)), 200, { "Content-Type": "text/csv; charset=utf-8" }));
  app.get("/bookings/:id", (c) => shown(c, bookings.findBooking(db, c.req.param("id"))));
  app.patch("/bookings/:id", async (c) => shown(c, bookings.reschedule(db, c.req.param("id"), await body(c))));
  app.post("/bookings/:id/cancel", (c) => {
    const refunded = bookings.cancel(db, c.req.param("id"));
    return c.json({ ...bookings.bookingJson(db, bookings.findBooking(db, c.req.param("id"))), refunded });
  });
  app.post("/bookings/:id/checkin", (c) => shown(c, bookings.mark(db, c.req.param("id"), "checked_in")));
  app.post("/bookings/:id/no-show", (c) => shown(c, bookings.noShow(db, c.req.param("id"))));
  app.post("/bookings/:id/move", async (c) => shown(c, bookings.move(db, c.req.param("id"), await body(c))));
  app.post("/bookings/:id/payments", async (c) => c.json(bookings.pay(db, c.req.param("id"), await body(c)), 201));
  app.get("/bookings/:id/payments", (c) => c.json(bookings.payments(db, c.req.param("id"))));
  app.get("/bookings/:id/history", (c) => c.json(bookings.history(db, c.req.param("id"))));
}

function otherRoutes(app: Hono, db: Db): void {
  app.get("/health", (c) => c.json({ status: "ok" }));
  app.post("/coupons", async (c) => {
    const made = bookings.createCoupon(db, await body(c));
    return c.json(made, 201);
  });
  app.get("/coupons/:code", (c) => c.json(bookings.couponJson(db, c.req.param("code"))));
  app.post("/series/:seriesId/cancel", (c) => c.json({ cancelled: bookings.cancelSeries(db, c.req.param("seriesId")) }));
  app.get("/reports/daily", (c) => c.json(bookings.daily(db, day(c.req.query("date")))));
  app.get("/reports/utilization", (c) => c.json(bookings.utilization(db, day(c.req.query("from")), day(c.req.query("to")))));
}

export function build(db: Db): Hono {
  const app = new Hono();
  otherRoutes(app, db);
  roomRoutes(app, db);
  closureRoutes(app, db);
  customerRoutes(app, db);
  bookingRoutes(app, db);
  app.notFound((c) => c.json({ error: "no such route" }, 404));
  app.onError((error, c) => {
    if (error instanceof Fail) return c.json({ error: error.message }, error.status as 400);
    console.error(error);
    return c.json({ error: "something went wrong" }, 500);
  });
  return app;
}
