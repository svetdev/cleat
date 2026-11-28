// Customers: who may book, and what they have booked.
import { all, Fail, given, id, one, round2, run, text, wholeOrNull } from "./core.ts";
import type { Db, Row } from "./core.ts";

export function customerJson(customer: Row): Row {
  return { id: customer.id, name: customer.name, email: customer.email, maxActiveBookings: customer.max_active, active: customer.active === 1 };
}

export function findCustomer(db: Db, customerId: unknown): Row {
  const customer = one(db, "SELECT * FROM customers WHERE id = ? AND deleted = 0", id(customerId));
  if (!customer) throw new Fail(404, "no such customer");
  return customer;
}

function fields(db: Db, body: Row, now: Row): Row {
  const name = text(given(body, "name", now.name), "name");
  const email = given(body, "email", now.email);
  if (typeof email !== "string" || !email.includes("@")) throw new Fail(400, "email must contain an @");
  if (one(db, "SELECT id FROM customers WHERE email = ? AND deleted = 0 AND id != ?", email, now.id ?? 0)) throw new Fail(409, "that email is taken");
  return { name, email, max_active: wholeOrNull(given(body, "maxActiveBookings", now.max_active ?? null), "maxActiveBookings") };
}

export function createCustomer(db: Db, body: Row): Row {
  const f = fields(db, body, {});
  return findCustomer(db, run(db, "INSERT INTO customers (name, email, max_active) VALUES (?, ?, ?)", f.name, f.email, f.max_active));
}

export function updateCustomer(db: Db, customerId: unknown, body: Row): Row {
  const now = findCustomer(db, customerId);
  const f = fields(db, body, now);
  run(db, "UPDATE customers SET name = ?, email = ?, max_active = ? WHERE id = ?", f.name, f.email, f.max_active, now.id);
  return findCustomer(db, now.id);
}

export function setActive(db: Db, customerId: unknown, active: number): Row {
  const customer = findCustomer(db, customerId);
  run(db, "UPDATE customers SET active = ? WHERE id = ?", active, customer.id);
  return findCustomer(db, customer.id);
}

// The customer, if they may take on another booking: 404 unknown, 403 not active, 409 at their limit.
export function mayBook(db: Db, customerId: unknown, counted = true): Row {
  const customer = findCustomer(db, customerId);
  if (customer.active !== 1) throw new Fail(403, "the customer is not active");
  const held = one(db, "SELECT COUNT(*) AS n FROM bookings WHERE customer_id = ? AND status = 'confirmed'", customer.id)?.n ?? 0;
  if (counted && customer.max_active !== null && held >= customer.max_active) throw new Fail(409, "the customer has as many bookings as they may");
  return customer;
}

export function summary(db: Db, customerId: unknown): Row {
  const customer = findCustomer(db, customerId);
  const rows = all(db, "SELECT status, price FROM bookings WHERE customer_id = ?", customer.id);
  const count = (status: string) => rows.filter((row) => row.status === status).length;
  const spent = rows.filter((row) => row.status === "confirmed").reduce((sum, row) => sum + row.price, 0);
  return { confirmed: count("confirmed"), cancelled: count("cancelled"), noShows: count("no_show"), totalSpent: round2(spent) };
}
