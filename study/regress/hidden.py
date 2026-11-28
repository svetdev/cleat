#!/usr/bin/env python3
"""hidden — the tests the agent never sees: one function per feature of spec.json, over HTTP.

  hidden.py <tree> [upto]     run the tests of features 1..upto (default: all) against the tree's app

Each feature's test gets its own start of the app on an empty database (app.py), so no
test leans on what another left behind; it builds what it needs through the API, which
means a later feature's test fails when an earlier feature's route is broken. A test
checks only what its feature's request states, and reads answers leniently: an id is
compared as text, a time by its instant, money to the cent, and extra fields are ignored,
because later features add fields to earlier answers.

`run(tree, upto)` returns {feature: {"ok": bool, "said": why not}}. A feature passes
when every check in its function holds. Nothing here is ever copied into an agent's tree.
"""

import concurrent.futures
import datetime
import json
import sys
import urllib.parse

import app as runner


class Wrong(Exception):
    """A check that did not hold."""


def check(holds, what):
    if not holds:
        raise Wrong(what)


class Api:
    """The app's routes, each call stating the status it expects. `calls` is every (method, path) made."""

    def __init__(self, app):
        self.app, self.calls = app, []

    def raw(self, method, path, want, body=None, headers=None):
        self.calls.append((method, path.split("?")[0]))
        reply = self.app.call(method, path, body, headers)
        if reply.status != want:
            sent = "" if body is None else " " + json.dumps(body)
            raise Wrong("%s %s%s answered %r, expected %d" % (method, path, sent, reply, want))
        return reply

    def get(self, path, want=200, **query):
        if query:
            path += "?" + urllib.parse.urlencode(query)
        return self.raw("GET", path, want).json

    def post(self, path, body=None, want=201, headers=None):
        return self.raw("POST", path, want, {} if body is None else body, headers).json

    def patch(self, path, body, want=200):
        return self.raw("PATCH", path, want, body).json

    def put(self, path, body, want=200):
        return self.raw("PUT", path, want, body).json

    def delete(self, path, want=204):
        return self.raw("DELETE", path, want).json


def T(day, clock="09:00"):
    return "2031-03-%02dT%s:00Z" % (day, clock)


def instant(text):
    check(isinstance(text, str), "a time is not text: %r" % (text,))
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Wrong("not an ISO time: %r" % text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def same(a, b):
    return instant(a) == instant(b)


def near(amount, expected):
    return isinstance(amount, (int, float)) and not isinstance(amount, bool) and abs(amount - expected) < 0.006


def ident(a, b):
    return str(a) == str(b)


def ids(items):
    return sorted(str(item["id"]) for item in items)


def room(api, name="Blue", capacity=4, **more):
    return api.post("/rooms", dict(name=name, capacity=capacity, **more))


def person(api, n=1):
    return api.post("/customers", {"name": "Person %d" % n, "email": "person%d@example.com" % n})


def slot(where, who, day, a="09:00", b="10:00", **more):
    return dict(roomId=where["id"], customerId=who["id"], start=T(day, a), end=T(day, b), **more)


def book(api, where, who, day, a="09:00", b="10:00", want=201, **more):
    return api.post("/bookings", slot(where, who, day, a, b, **more), want)


def booking(api, made):
    return api.get("/bookings/%s" % made["id"])


def spans(free):
    return [(instant(item["start"]), instant(item["end"])) for item in free]


def f01(api):
    check(api.get("/health").get("status") == "ok", "GET /health is not {status: ok}")
    blue = api.post("/rooms", {"name": "Blue", "capacity": 4})
    check(blue["name"] == "Blue" and blue["capacity"] == 4 and "id" in blue, "the new room is not {id, name, capacity}: %r" % blue)
    for bad in ({"name": "", "capacity": 4}, {"name": "X"}, {"capacity": 3}, {"name": "X", "capacity": 0},
                {"name": "X", "capacity": -1}, {"name": "X", "capacity": 2.5}):
        api.post("/rooms", bad, 400)
    rooms = api.get("/rooms")
    check(ids(rooms) == ids([blue]), "GET /rooms is not the one room made: %r" % rooms)
    check(api.get("/rooms/%s" % blue["id"])["name"] == "Blue", "GET /rooms/:id is not the room")
    missing = api.raw("GET", "/rooms/999999", 404)
    check(isinstance(missing.json, dict) and "error" in missing.json, "an error is not {error: ...}: %r" % missing)
    api.app.restart()
    check(api.get("/rooms/%s" % blue["id"])["name"] == "Blue", "the room was lost when the server restarted")


def f02(api):
    ann = api.post("/customers", {"name": "Ann", "email": "ann@example.com"})
    check(ann["name"] == "Ann" and ann["email"] == "ann@example.com" and "id" in ann, "the new customer is not {id, name, email}")
    api.post("/customers", {"name": "", "email": "x@example.com"}, 400)
    api.post("/customers", {"name": "Bo", "email": "no-at-sign"}, 400)
    api.post("/customers", {"name": "Bo"}, 400)
    api.post("/customers", {"name": "Other Ann", "email": "ann@example.com"}, 409)
    bo = api.post("/customers", {"name": "Bo", "email": "bo@example.com"})
    check(ids(api.get("/customers")) == ids([ann, bo]), "GET /customers is not the two customers made")
    check(api.get("/customers/%s" % bo["id"])["email"] == "bo@example.com", "GET /customers/:id is not the customer")
    api.get("/customers/999999", 404)


def f03(api):
    blue, ann = room(api), person(api)
    made = book(api, blue, ann, 4)
    check(ident(made["roomId"], blue["id"]) and ident(made["customerId"], ann["id"]), "the booking's roomId or customerId is wrong")
    check(same(made["start"], T(4, "09:00")) and same(made["end"], T(4, "10:00")), "the booking's times are not the ones sent")
    check(made["status"] == "confirmed", "a new booking is not confirmed")
    book(api, blue, ann, 5, "10:00", "09:00", want=400)
    book(api, blue, ann, 5, "10:00", "10:00", want=400)
    api.post("/bookings", {"roomId": blue["id"], "customerId": ann["id"], "end": T(5)}, 400)
    api.post("/bookings", {"roomId": blue["id"], "customerId": ann["id"], "start": "next tuesday", "end": T(5)}, 400)
    api.post("/bookings", {"roomId": 999999, "customerId": ann["id"], "start": T(6), "end": T(6, "10:00")}, 404)
    api.post("/bookings", {"roomId": blue["id"], "customerId": 999999, "start": T(6), "end": T(6, "10:00")}, 404)
    check(same(booking(api, made)["start"], T(4, "09:00")), "GET /bookings/:id is not the booking")
    api.get("/bookings/999999", 404)
    check(ids(api.get("/bookings")) == ids([made]), "GET /bookings is not the one booking made")


def f04(api):
    blue, green, ann = room(api), room(api, "Green"), person(api)
    book(api, blue, ann, 4, "09:00", "11:00")
    for a, b in (("09:00", "11:00"), ("10:00", "12:00"), ("08:00", "09:30"), ("09:30", "10:30"), ("08:00", "12:00")):
        book(api, blue, ann, 4, a, b, want=409)
    book(api, blue, ann, 4, "11:00", "12:00")
    book(api, blue, ann, 4, "08:00", "09:00")
    book(api, green, ann, 4, "09:00", "11:00")
    check(len(api.get("/bookings")) == 4, "a refused booking was stored, or an accepted one was not")


def f05(api):
    blue, ann = room(api), person(api)
    made = book(api, blue, ann, 4)
    gone = api.post("/bookings/%s/cancel" % made["id"], want=200)
    check(gone["status"] == "cancelled" and ident(gone["id"], made["id"]), "the cancelled booking is not returned as cancelled")
    check(booking(api, made)["status"] == "cancelled", "the booking is not cancelled afterwards")
    api.post("/bookings/%s/cancel" % made["id"], want=409)
    api.post("/bookings/999999/cancel", want=404)
    again = book(api, blue, ann, 4)
    check(again["status"] == "confirmed", "the freed time could not be booked again")


def f06(api):
    blue, green, ann, bo = room(api), room(api, "Green"), person(api, 1), person(api, 2)
    one = book(api, blue, ann, 4, "09:00", "10:00")
    two = book(api, blue, bo, 4, "13:00", "15:00")
    three = book(api, green, ann, 5, "09:00", "10:00")
    four = book(api, green, bo, 6, "09:00", "10:00")
    api.post("/bookings/%s/cancel" % four["id"], want=200)
    check(ids(api.get("/bookings", roomId=blue["id"])) == ids([one, two]), "roomId does not filter")
    check(ids(api.get("/bookings", customerId=ann["id"])) == ids([one, three]), "customerId does not filter")
    check(ids(api.get("/bookings", status="cancelled")) == ids([four]), "status does not filter")
    check(ids(api.get("/bookings", status="confirmed", roomId=green["id"])) == ids([three]), "filters do not combine")
    window = api.get("/bookings", **{"from": T(4, "09:30"), "to": T(4, "14:00")})
    check(ids(window) == ids([one, two]), "from and to do not keep the bookings that overlap the window")
    edge = api.get("/bookings", **{"from": T(4, "10:00"), "to": T(4, "13:00")})
    check(ids(edge) == [], "a booking that only touches the window's edge is in it")
    check(len(api.get("/bookings")) == 4, "GET /bookings without filters is not everything")


def f07(api):
    blue, ann = room(api), person(api)
    whole = api.get("/rooms/%s/availability" % blue["id"], date="2031-03-04")
    check(whole["date"] == "2031-03-04", "the answer's date is not the one asked for")
    check(spans(whole["free"]) == [(instant(T(4, "00:00")), instant(T(5, "00:00")))], "an empty day is not one interval from midnight to midnight")
    book(api, blue, ann, 4, "13:00", "15:00")
    book(api, blue, ann, 4, "09:00", "10:00")
    dropped = book(api, blue, ann, 4, "20:00", "21:00")
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    free = spans(api.get("/rooms/%s/availability" % blue["id"], date="2031-03-04")["free"])
    expected = [(T(4, "00:00"), T(4, "09:00")), (T(4, "10:00"), T(4, "13:00")), (T(4, "15:00"), T(5, "00:00"))]
    check(free == [(instant(a), instant(b)) for a, b in expected], "the free intervals around two bookings are wrong: %r" % free)
    api.get("/rooms/%s/availability" % blue["id"], 400)
    api.get("/rooms/%s/availability" % blue["id"], 400, date="tomorrow")
    api.get("/rooms/%s/availability" % blue["id"], 400, date="2031-13-45")
    api.get("/rooms/999999/availability", 404, date="2031-03-04")


def f08(api):
    blue, ann = room(api), person(api)
    renamed = api.patch("/rooms/%s" % blue["id"], {"name": "Azure"})
    check(renamed["name"] == "Azure" and renamed["capacity"] == 4, "PATCH did not change the name and keep the capacity")
    check(api.patch("/rooms/%s" % blue["id"], {"capacity": 6})["capacity"] == 6, "PATCH did not change the capacity")
    check(api.get("/rooms/%s" % blue["id"])["name"] == "Azure", "the change was not kept")
    api.patch("/rooms/%s" % blue["id"], {"capacity": 0}, 400)
    api.patch("/rooms/%s" % blue["id"], {"name": ""}, 400)
    api.patch("/rooms/999999", {"name": "X"}, 404)
    made = book(api, blue, ann, 4)
    api.delete("/rooms/%s" % blue["id"], 409)
    api.post("/bookings/%s/cancel" % made["id"], want=200)
    api.delete("/rooms/%s" % blue["id"])
    api.get("/rooms/%s" % blue["id"], 404)
    check(api.get("/rooms") == [], "a removed room is still listed")
    api.delete("/rooms/%s" % blue["id"], 404)


def f09(api):
    blue, ann = room(api, capacity=4), person(api)
    check(book(api, blue, ann, 4)["attendees"] == 1, "attendees is not 1 when left out")
    three = book(api, blue, ann, 5, attendees=3)
    check(three["attendees"] == 3 and booking(api, three)["attendees"] == 3, "attendees is not shown on the booking")
    check(book(api, blue, ann, 6, attendees=4)["attendees"] == 4, "a booking for exactly the capacity was refused")
    for bad in (5, 0, -2, 1.5):
        book(api, blue, ann, 7, attendees=bad, want=400)


def f10(api):
    blue, ann = room(api, capacity=4), person(api)
    first = book(api, blue, ann, 4, "09:00", "10:00")
    other = book(api, blue, ann, 4, "13:00", "14:00")
    moved = api.patch("/bookings/%s" % first["id"], {"start": T(4, "11:00"), "end": T(4, "12:00")})
    check(same(moved["start"], T(4, "11:00")) and same(moved["end"], T(4, "12:00")), "PATCH did not move the booking")
    check(same(booking(api, first)["start"], T(4, "11:00")), "the move was not kept")
    api.patch("/bookings/%s" % first["id"], {"start": T(4, "13:30"), "end": T(4, "14:30")}, 409)
    shifted = api.patch("/bookings/%s" % first["id"], {"start": T(4, "11:30"), "end": T(4, "12:30")})
    check(same(shifted["end"], T(4, "12:30")), "a booking could not be shifted over its own old time")
    check(same(api.patch("/bookings/%s" % first["id"], {"end": T(4, "12:45")})["start"], T(4, "11:30")), "changing only the end lost the start")
    api.patch("/bookings/%s" % first["id"], {"end": T(4, "11:00")}, 400)
    api.patch("/bookings/%s" % first["id"], {"attendees": 5}, 400)
    check(api.patch("/bookings/%s" % first["id"], {"attendees": 2})["attendees"] == 2, "PATCH did not change attendees")
    api.patch("/bookings/999999", {"attendees": 2}, 404)
    api.post("/bookings/%s/cancel" % other["id"], want=200)
    api.patch("/bookings/%s" % other["id"], {"attendees": 2}, 409)
    book(api, blue, ann, 4, "09:00", "10:00")


def f11(api):
    blue, open_room, ann = room(api), room(api, "Open"), person(api)
    hours = "/rooms/%s/hours" % blue["id"]
    check(api.get(hours) == {"open": None, "close": None}, "hours that were never set are not {open: null, close: null}")
    check(api.put(hours, {"open": "08:00", "close": "18:00"}) == {"open": "08:00", "close": "18:00"}, "PUT hours does not answer {open, close}")
    check(api.get(hours) == {"open": "08:00", "close": "18:00"}, "the hours were not kept")
    api.put(hours, {"open": "18:00", "close": "08:00"}, 400)
    api.put(hours, {"open": "8am", "close": "18:00"}, 400)
    api.put("/rooms/999999/hours", {"open": "08:00", "close": "18:00"}, 404)
    book(api, blue, ann, 4, "07:00", "09:00", want=400)
    book(api, blue, ann, 4, "17:00", "19:00", want=400)
    api.post("/bookings", {"roomId": blue["id"], "customerId": ann["id"], "start": T(4, "17:00"), "end": T(5, "09:00")}, 400)
    inside = book(api, blue, ann, 4, "09:00", "10:00")
    book(api, blue, ann, 4, "08:00", "09:00")
    book(api, blue, ann, 4, "17:00", "18:00")
    api.patch("/bookings/%s" % inside["id"], {"start": T(4, "18:00"), "end": T(4, "19:00")}, 400)
    empty = spans(api.get("/rooms/%s/availability" % blue["id"], date="2031-03-06")["free"])
    check(empty == [(instant(T(6, "08:00")), instant(T(6, "18:00")))], "availability does not stay inside the opening hours: %r" % empty)
    busy = spans(api.get("/rooms/%s/availability" % blue["id"], date="2031-03-04")["free"])
    check(busy == [(instant(T(4, "10:00")), instant(T(4, "17:00")))], "availability inside opening hours is wrong: %r" % busy)
    book(api, open_room, ann, 4, "05:00", "06:00")
    api.delete(hours)
    check(api.get(hours) == {"open": None, "close": None}, "DELETE did not remove the hours")
    book(api, blue, ann, 7, "05:00", "06:00")


def f12(api):
    blue, ann = room(api), person(api)
    closures = "/rooms/%s/closures" % blue["id"]
    shut = api.post(closures, {"date": "2031-03-05", "reason": "painting"})
    check(shut["date"] == "2031-03-05" and shut["reason"] == "painting" and "id" in shut, "the closure is not {id, date, reason}")
    check(ids(api.get(closures)) == ids([shut]), "GET closures is not the one closure made")
    api.post(closures, {"date": "the fifth"}, 400)
    api.post("/rooms/999999/closures", {"date": "2031-03-05"}, 404)
    book(api, blue, ann, 5, want=409)
    api.post("/bookings", {"roomId": blue["id"], "customerId": ann["id"], "start": T(4, "23:00"), "end": T(5, "01:00")}, 409)
    before = book(api, blue, ann, 4)
    api.patch("/bookings/%s" % before["id"], {"start": T(5, "09:00"), "end": T(5, "10:00")}, 409)
    check(api.get("/rooms/%s/availability" % blue["id"], date="2031-03-05")["free"] == [], "a closed day has free intervals")
    check(len(api.get("/rooms/%s/availability" % blue["id"], date="2031-03-06")["free"]) == 1, "the day after a closure is not free")
    api.delete("%s/999999" % closures, 404)
    api.delete("%s/%s" % (closures, shut["id"]))
    check(api.get(closures) == [], "the closure was not removed")
    book(api, blue, ann, 5)


def f13(api):
    blue, plain, ann = room(api, hourlyRate=40), room(api, "Plain"), person(api)
    check(near(blue["hourlyRate"], 40) and near(api.get("/rooms/%s" % blue["id"])["hourlyRate"], 40), "the room does not show its hourlyRate")
    check(near(plain["hourlyRate"], 0), "hourlyRate is not 0 when left out")
    check(near(book(api, plain, ann, 4)["price"], 0), "a booking in a room without a rate does not cost 0")
    two_hours = book(api, blue, ann, 4, "09:00", "11:00")
    check(near(two_hours["price"], 80), "2 hours at 40 is not 80: %r" % two_hours.get("price"))
    check(near(book(api, blue, ann, 4, "12:00", "13:30")["price"], 60), "90 minutes at 40 is not 60")
    check(near(book(api, blue, ann, 4, "14:00", "14:20")["price"], 13.33), "20 minutes at 40 is not 13.33")
    check(near(api.patch("/rooms/%s" % blue["id"], {"hourlyRate": 50})["hourlyRate"], 50), "PATCH did not change the rate")
    check(near(booking(api, two_hours)["price"], 80), "an existing booking's price changed with the room's rate")
    check(near(book(api, blue, ann, 5, "09:00", "10:00")["price"], 50), "a new booking does not use the new rate")
    longer = api.patch("/bookings/%s" % two_hours["id"], {"end": T(4, "12:00")})
    check(near(longer["price"], 150), "a rescheduled booking is not priced again at the room's rate: %r" % longer.get("price"))
    api.patch("/rooms/%s" % blue["id"], {"hourlyRate": -5}, 400)
    api.post("/rooms", {"name": "Bad", "capacity": 2, "hourlyRate": -1}, 400)


def f14(api):
    blue, ann, bo = room(api, hourlyRate=20), person(api, 1), person(api, 2)
    one = book(api, blue, ann, 4, "09:00", "11:00")
    two = book(api, blue, ann, 5, "09:00", "10:00")
    three = book(api, blue, ann, 6, "09:00", "12:00")
    book(api, blue, bo, 7)
    api.post("/bookings/%s/cancel" % three["id"], want=200)
    check(ids(api.get("/customers/%s/bookings" % ann["id"])) == ids([one, two, three]), "a customer's bookings are not all of theirs")
    summary = api.get("/customers/%s/summary" % ann["id"])
    check(summary["confirmed"] == 2 and summary["cancelled"] == 1, "the summary's counts are wrong: %r" % summary)
    check(near(summary["totalSpent"], 60), "totalSpent is not the confirmed bookings' prices: %r" % summary)
    api.get("/customers/999999/bookings", 404)
    api.get("/customers/999999/summary", 404)


def f15(api):
    blue, ann = room(api, hourlyRate=40), person(api)
    spring = api.post("/coupons", {"code": "SPRING", "percentOff": 25})
    check(spring["code"] == "SPRING" and near(spring["percentOff"], 25), "the coupon is not {code, percentOff}")
    api.post("/coupons", {"code": "SPRING", "percentOff": 10}, 409)
    for bad in ({"code": "ZERO", "percentOff": 0}, {"code": "BIG", "percentOff": 150}, {"percentOff": 10}, {"code": "", "percentOff": 10}):
        api.post("/coupons", bad, 400)
    cut = book(api, blue, ann, 4, "09:00", "11:00", couponCode="SPRING")
    check(near(cut["price"], 60) and near(cut["discount"], 20) and cut["couponCode"] == "SPRING", "25%% off 80 is not price 60, discount 20: %r" % cut)
    full = book(api, blue, ann, 5, "09:00", "11:00")
    check(near(full["price"], 80) and near(full["discount"], 0), "a booking without a coupon is not full price with discount 0")
    book(api, blue, ann, 6, couponCode="NOPE", want=400)
    longer = api.patch("/bookings/%s" % cut["id"], {"end": T(4, "13:00")})
    check(near(longer["price"], 120) and near(longer["discount"], 40) and longer["couponCode"] == "SPRING", "a rescheduled booking lost or miscounted its coupon: %r" % longer)


def f16(api):
    blue, ann = room(api, hourlyRate=10), person(api)
    api.post("/coupons", {"code": "TWICE", "percentOff": 10, "maxUses": 2})
    api.post("/coupons", {"code": "ALWAYS", "percentOff": 10})
    check(api.get("/coupons/ALWAYS")["maxUses"] is None, "maxUses is not null for a coupon without a limit")
    fresh = api.get("/coupons/TWICE")
    check(fresh["maxUses"] == 2 and fresh["uses"] == 0 and near(fresh["percentOff"], 10), "GET /coupons/:code is wrong: %r" % fresh)
    for bad in (0, -1, 1.5):
        api.post("/coupons", {"code": "BAD%s" % bad, "percentOff": 10, "maxUses": bad}, 400)
    first = book(api, blue, ann, 4, couponCode="TWICE")
    book(api, blue, ann, 5, couponCode="TWICE")
    check(api.get("/coupons/TWICE")["uses"] == 2, "uses does not count the confirmed bookings with the coupon")
    book(api, blue, ann, 6, couponCode="TWICE", want=409)
    api.post("/bookings/%s/cancel" % first["id"], want=200)
    check(api.get("/coupons/TWICE")["uses"] == 1, "cancelling did not give the use back")
    book(api, blue, ann, 6, couponCode="TWICE")
    api.get("/coupons/NOPE", 404)


def f17(api):
    blue, ann = room(api, hourlyRate=40), person(api)
    made = book(api, blue, ann, 4, "09:00", "11:00")
    check(near(made["paid"], 0) and near(made["balance"], 80), "a new booking is not paid 0, balance 80: %r" % made)
    payments = "/bookings/%s/payments" % made["id"]
    part = api.post(payments, {"amount": 30})
    check(near(part["amount"], 30) and ident(part["bookingId"], made["id"]) and "id" in part, "the payment is not {id, bookingId, amount}")
    now = booking(api, made)
    check(near(now["paid"], 30) and near(now["balance"], 50), "after paying 30 of 80 the booking is not paid 30, balance 50: %r" % now)
    for bad in (60, 0, -5):
        api.post(payments, {"amount": bad}, 400)
    api.post(payments, {}, 400)
    api.post(payments, {"amount": 50})
    check(near(booking(api, made)["balance"], 0), "paying the rest did not bring the balance to 0")
    check(sorted(p["amount"] for p in api.get(payments)) == [30, 50], "GET payments is not the two payments made")
    api.post("/bookings/999999/payments", {"amount": 5}, 404)
    api.get("/bookings/999999/payments", 404)
    dropped = book(api, blue, ann, 5)
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    api.post("/bookings/%s/payments" % dropped["id"], {"amount": 5}, 409)


def f18(api):
    blue, ann = room(api, hourlyRate=40), person(api)
    made = book(api, blue, ann, 4, "09:00", "11:00")
    api.post("/bookings/%s/payments" % made["id"], {"amount": 30})
    api.post("/bookings/%s/payments" % made["id"], {"amount": 20})
    gone = api.post("/bookings/%s/cancel" % made["id"], want=200)
    check(near(gone["refunded"], 50), "cancelling a booking paid 50 does not answer refunded 50: %r" % gone.get("refunded"))
    after = booking(api, made)
    check(near(after["paid"], 0) and near(after["balance"], 0), "after the refund the booking is not paid 0, balance 0: %r" % after)
    amounts = sorted(p["amount"] for p in api.get("/bookings/%s/payments" % made["id"]))
    check(len(amounts) == 3 and near(amounts[0], -50), "the refund is not recorded as a payment of -50: %r" % amounts)
    unpaid = book(api, blue, ann, 5)
    check(near(api.post("/bookings/%s/cancel" % unpaid["id"], want=200)["refunded"], 0), "cancelling an unpaid booking does not answer refunded 0")
    check(api.get("/bookings/%s/payments" % unpaid["id"]) == [], "a refund was recorded for a booking nobody paid for")


def f19(api):
    blue, plain, ann = room(api, minMinutes=30, maxMinutes=120), room(api, "Plain"), person(api)
    check(blue["minMinutes"] == 30 and blue["maxMinutes"] == 120, "the room does not show minMinutes and maxMinutes")
    check(plain["minMinutes"] is None and plain["maxMinutes"] is None, "the limits are not null when left out")
    book(api, blue, ann, 4, "09:00", "09:15", want=400)
    book(api, blue, ann, 4, "09:00", "12:00", want=400)
    book(api, blue, ann, 4, "09:00", "09:30")
    two_hours = book(api, blue, ann, 4, "10:00", "12:00")
    api.patch("/bookings/%s" % two_hours["id"], {"end": T(4, "10:10")}, 400)
    api.patch("/bookings/%s" % two_hours["id"], {"end": T(4, "13:00")}, 400)
    book(api, plain, ann, 4, "09:00", "09:05")
    for bad in ({"minMinutes": 0}, {"maxMinutes": "long"}, {"minMinutes": 2.5}):
        api.patch("/rooms/%s" % blue["id"], bad, 400)
    lifted = api.patch("/rooms/%s" % blue["id"], {"maxMinutes": None})
    check(lifted["maxMinutes"] is None and lifted["minMinutes"] == 30, "PATCH maxMinutes: null did not lift only that limit")
    book(api, blue, ann, 5, "09:00", "14:00")
    check(api.patch("/rooms/%s" % plain["id"], {"minMinutes": 45})["minMinutes"] == 45, "PATCH did not set minMinutes")
    book(api, plain, ann, 5, "09:00", "09:30", want=400)


def f20(api):
    blue, plain, ann = room(api, bufferMinutes=15), room(api, "Plain"), person(api)
    check(blue["bufferMinutes"] == 15 and plain["bufferMinutes"] == 0, "the room does not show bufferMinutes, 0 when left out")
    book(api, blue, ann, 4, "09:00", "10:00")
    for a, b in (("10:00", "11:00"), ("10:10", "11:00"), ("08:00", "08:50"), ("08:00", "09:00")):
        book(api, blue, ann, 4, a, b, want=409)
    after = book(api, blue, ann, 4, "10:15", "11:00")
    book(api, blue, ann, 4, "08:00", "08:45")
    api.patch("/bookings/%s" % after["id"], {"start": T(4, "10:05")}, 409)
    check(same(api.patch("/bookings/%s" % after["id"], {"end": T(4, "11:30")})["end"], T(4, "11:30")), "a booking could not be lengthened away from its neighbours")
    dropped = book(api, blue, ann, 5, "09:00", "10:00")
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    book(api, blue, ann, 5, "10:00", "11:00")
    book(api, plain, ann, 4, "09:00", "10:00")
    book(api, plain, ann, 4, "10:00", "11:00")
    api.patch("/rooms/%s" % plain["id"], {"bufferMinutes": -1}, 400)
    check(api.patch("/rooms/%s" % plain["id"], {"bufferMinutes": 30})["bufferMinutes"] == 30, "PATCH did not set bufferMinutes")
    book(api, plain, ann, 4, "11:15", "12:00", want=409)


def f21(api):
    blue, ann = room(api), person(api)
    single = book(api, blue, ann, 2)
    check(single["seriesId"] is None, "seriesId is not null on a booking outside a series")
    weekly = api.post("/bookings/recurring", slot(blue, ann, 3, repeat="weekly", count=3))
    check(weekly["seriesId"] is not None and len(weekly["bookings"]) == 3, "the answer is not {seriesId, bookings} with 3 bookings")
    for made, day in zip(weekly["bookings"], (3, 10, 17)):
        check(same(made["start"], T(day, "09:00")) and same(made["end"], T(day, "10:00")), "a weekly booking is not a week after the one before")
        check(ident(made["seriesId"], weekly["seriesId"]) and made["status"] == "confirmed", "a booking of the series lacks its seriesId")
    check(ids(api.get("/bookings", seriesId=weekly["seriesId"])) == ids(weekly["bookings"]), "seriesId does not filter GET /bookings")
    daily = api.post("/bookings/recurring", slot(blue, ann, 24, "14:00", "15:00", repeat="daily", count=2, attendees=2))
    check(same(daily["bookings"][1]["start"], T(25, "14:00")) and daily["bookings"][1]["attendees"] == 2, "a daily series is not a day apart with its attendees")
    check(not ident(daily["seriesId"], weekly["seriesId"]), "two series share a seriesId")
    book(api, blue, ann, 20, "11:00", "12:00")
    count = len(api.get("/bookings"))
    api.post("/bookings/recurring", slot(blue, ann, 6, "11:00", "12:00", repeat="weekly", count=3), 409)
    check(len(api.get("/bookings")) == count, "a series that could not be made left bookings behind")
    book(api, blue, ann, 6, "11:00", "12:00")
    for bad in (dict(repeat="monthly", count=3), dict(repeat="weekly", count=1), dict(repeat="weekly", count=53), dict(repeat="weekly")):
        api.post("/bookings/recurring", slot(blue, ann, 7, "16:00", "17:00", **bad), 400)
    api.post("/bookings/recurring", dict(slot(blue, ann, 7, "16:00", "17:00", repeat="daily", count=2), roomId=999999), 404)


def f22(api):
    blue, ann = room(api), person(api)
    series = api.post("/bookings/recurring", slot(blue, ann, 3, repeat="weekly", count=3))
    apart = book(api, blue, ann, 4)
    api.post("/bookings/%s/cancel" % series["bookings"][0]["id"], want=200)
    check(api.post("/series/%s/cancel" % series["seriesId"], want=200) == {"cancelled": 2}, "cancelling a series of 3 with one already cancelled is not {cancelled: 2}")
    check(all(booking(api, made)["status"] == "cancelled" for made in series["bookings"]), "a booking of the series is not cancelled")
    check(booking(api, apart)["status"] == "confirmed", "a booking outside the series was cancelled")
    check(api.post("/series/%s/cancel" % series["seriesId"], want=200) == {"cancelled": 0}, "cancelling the series again is not {cancelled: 0}")
    book(api, blue, ann, 10)
    api.post("/series/999999/cancel", want=404)


def f23(api):
    blue, ann, bo = room(api), person(api, 1), person(api, 2)
    waitlist = "/rooms/%s/waitlist" % blue["id"]
    book(api, blue, ann, 4, "09:00", "11:00")
    first = api.post(waitlist, {"customerId": bo["id"], "start": T(4, "09:00"), "end": T(4, "10:00")})
    check(ident(first["roomId"], blue["id"]) and ident(first["customerId"], bo["id"]) and same(first["start"], T(4, "09:00")) and same(first["end"], T(4, "10:00")),
          "the entry is not {id, roomId, customerId, start, end}: %r" % first)
    second = api.post(waitlist, {"customerId": ann["id"], "start": T(4, "10:00"), "end": T(4, "12:00")})
    check([str(e["id"]) for e in api.get(waitlist)] == [str(first["id"]), str(second["id"])], "GET waitlist is not the entries, oldest first")
    api.post(waitlist, {"customerId": bo["id"], "start": T(5, "09:00"), "end": T(5, "10:00")}, 409)
    api.post(waitlist, {"customerId": bo["id"], "start": T(4, "10:00"), "end": T(4, "09:00")}, 400)
    api.post(waitlist, {"customerId": 999999, "start": T(4, "09:00"), "end": T(4, "10:00")}, 404)
    api.post("/rooms/999999/waitlist", {"customerId": bo["id"], "start": T(4, "09:00"), "end": T(4, "10:00")}, 404)
    api.delete("%s/%s" % (waitlist, first["id"]))
    check(ids(api.get(waitlist)) == ids([second]), "the entry was not removed")
    api.delete("%s/999999" % waitlist, 404)


def f24(api):
    blue, ann, bo, cy = room(api), person(api, 1), person(api, 2), person(api, 3)
    waitlist = "/rooms/%s/waitlist" % blue["id"]
    taken = book(api, blue, ann, 4, "09:00", "10:00")
    check(taken["fromWaitlist"] is False, "fromWaitlist is not false on a normal booking")
    api.post(waitlist, {"customerId": bo["id"], "start": T(4, "09:00"), "end": T(4, "10:00")})
    later = api.post(waitlist, {"customerId": cy["id"], "start": T(4, "09:00"), "end": T(4, "10:00")})
    api.post("/bookings/%s/cancel" % taken["id"], want=200)
    promoted = [b for b in api.get("/bookings", customerId=bo["id"]) if b["status"] == "confirmed"]
    check(len(promoted) == 1 and promoted[0]["fromWaitlist"] is True, "the oldest waiting entry did not become a booking with fromWaitlist true")
    check(same(promoted[0]["start"], T(4, "09:00")) and ident(promoted[0]["roomId"], blue["id"]), "the booking from the waiting list has the wrong room or time")
    check(ids(api.get(waitlist)) == ids([later]), "the waiting list is not just the entry that still does not fit")
    check(api.get("/bookings", customerId=cy["id"]) == [], "a second entry for the same time was booked as well")
    api.post("/bookings/%s/cancel" % promoted[0]["id"], want=200)
    check(len([b for b in api.get("/bookings", customerId=cy["id"]) if b["status"] == "confirmed"]) == 1, "the next entry was not booked when the time came free again")
    check(api.get(waitlist) == [], "the waiting list is not empty once everyone is booked")
    block = book(api, blue, ann, 6, "09:00", "10:00")
    book(api, blue, ann, 6, "10:00", "11:00")
    long_wait = api.post(waitlist, {"customerId": bo["id"], "start": T(6, "09:00"), "end": T(6, "11:00")})
    api.post("/bookings/%s/cancel" % block["id"], want=200)
    check(ids(api.get(waitlist)) == ids([long_wait]), "an entry that still does not fit left the waiting list")


def f25(api):
    blue, ann, bo = room(api), person(api, 1), person(api, 2)
    check(ann["maxActiveBookings"] is None, "maxActiveBookings is not null unless set")
    limited = api.patch("/customers/%s" % ann["id"], {"maxActiveBookings": 2})
    check(limited["maxActiveBookings"] == 2 and limited["name"] == "Person 1", "PATCH did not set maxActiveBookings and keep the name")
    first = book(api, blue, ann, 4)
    book(api, blue, ann, 5)
    book(api, blue, ann, 6, want=409)
    api.post("/bookings/%s/cancel" % first["id"], want=200)
    book(api, blue, ann, 6)
    for day in (7, 8, 9):
        book(api, blue, bo, day)
    check(api.patch("/customers/%s" % ann["id"], {"name": "Ann"})["name"] == "Ann", "PATCH did not change the name")
    check(api.get("/customers/%s" % ann["id"])["maxActiveBookings"] == 2, "changing the name lost the limit")
    api.patch("/customers/%s" % ann["id"], {"email": "person2@example.com"}, 409)
    check(api.patch("/customers/%s" % ann["id"], {"email": "person1@example.com"})["email"] == "person1@example.com", "a customer could not be saved with their own email")
    for bad in ({"maxActiveBookings": 0}, {"maxActiveBookings": 1.5}, {"name": ""}, {"email": "nope"}):
        api.patch("/customers/%s" % ann["id"], bad, 400)
    api.patch("/customers/999999", {"name": "X"}, 404)
    check(api.patch("/customers/%s" % ann["id"], {"maxActiveBookings": None})["maxActiveBookings"] is None, "PATCH maxActiveBookings: null did not lift the limit")
    book(api, blue, ann, 10)


def f26(api):
    blue, ann, bo = room(api), person(api, 1), person(api, 2)
    check(ann["active"] is True, "a new customer is not active")
    had = book(api, blue, ann, 4, "09:00", "11:00")
    off = api.post("/customers/%s/deactivate" % ann["id"], want=200)
    check(off["active"] is False and api.get("/customers/%s" % ann["id"])["active"] is False, "deactivate did not set active to false")
    book(api, blue, ann, 5, want=403)
    api.post("/bookings/recurring", slot(blue, ann, 6, repeat="daily", count=2), 403)
    book(api, blue, bo, 7)
    api.post("/rooms/%s/waitlist" % blue["id"], {"customerId": ann["id"], "start": T(7), "end": T(7, "10:00")}, 403)
    check(booking(api, had)["status"] == "confirmed", "deactivating a customer changed a booking they had")
    check(api.post("/customers/%s/activate" % ann["id"], want=200)["active"] is True, "activate did not set active to true")
    book(api, blue, ann, 5)
    api.post("/customers/999999/deactivate", want=404)
    api.post("/customers/999999/activate", want=404)


def f27(api):
    blue, ann = room(api), person(api)
    standup = book(api, blue, ann, 4, title="Daily Standup", notes="bring coffee")
    check(standup["title"] == "Daily Standup" and standup["notes"] == "bring coffee", "the booking does not show its title and notes")
    review = book(api, blue, ann, 5, title="Budget review")
    book(api, blue, ann, 6)
    book(api, blue, ann, 7, title="x" * 101, want=400)
    book(api, blue, ann, 7, title="y" * 100)
    check(ids(api.get("/bookings", q="stand")) == ids([standup]), "q does not find a title whatever the capitals")
    check(ids(api.get("/bookings", q="REVIEW")) == ids([review]), "q does not find a title whatever the capitals")
    check(api.get("/bookings", q="nothing like this") == [], "q keeps bookings whose title does not contain it")
    renamed = api.patch("/bookings/%s" % standup["id"], {"title": "Weekly sync", "notes": "no coffee"})
    check(renamed["title"] == "Weekly sync" and renamed["notes"] == "no coffee" and same(renamed["start"], T(4)), "PATCH did not change the title and notes and keep the time")
    api.patch("/bookings/%s" % standup["id"], {"title": "z" * 101}, 400)
    check(ids(api.get("/bookings", q="sync")) == ids([standup]) and api.get("/bookings", q="stand") == [], "q does not follow a changed title")


def f28(api):
    blue, green, ann = room(api), room(api, "Green"), person(api)
    made = {day: book(api, blue if day != 6 else green, ann, day) for day in (8, 4, 6, 10, 5)}
    order = [str(made[day]["id"]) for day in (4, 5, 6, 8, 10)]
    listing = lambda **query: api.raw("GET", "/bookings?" + urllib.parse.urlencode(query), 200)   # noqa: E731
    up = listing(sort="start")
    check([str(b["id"]) for b in up.json] == order, "sort=start is not earliest first")
    check(up.headers.get("X-Total-Count") == "5", "X-Total-Count is not the number of bookings: %r" % up.headers.get("X-Total-Count"))
    check([str(b["id"]) for b in listing(sort="-start").json] == order[::-1], "sort=-start is not latest first")
    page = listing(sort="start", limit=2)
    check([str(b["id"]) for b in page.json] == order[:2] and page.headers.get("X-Total-Count") == "5", "limit=2 is not the first two with the total in the header")
    check([str(b["id"]) for b in listing(sort="start", limit=2, offset=4).json] == order[4:], "limit and offset do not give the last page")
    check([str(b["id"]) for b in listing(sort="start", offset=3).json] == order[3:], "offset without limit does not skip")
    filtered = listing(roomId=blue["id"], sort="start", limit=1)
    check(filtered.headers.get("X-Total-Count") == "4" and len(filtered.json) == 1, "X-Total-Count does not count what matches the filters")
    check(listing(limit=0).json == [], "limit=0 is not an empty page")
    for bad in (dict(limit=-1), dict(limit="many"), dict(offset=-2), dict(offset="x")):
        api.raw("GET", "/bookings?" + urllib.parse.urlencode(bad), 400)
    plain = api.raw("GET", "/bookings", 200)
    check(len(plain.json) == 5 and plain.headers.get("X-Total-Count") == "5", "GET /bookings without limit is not everything, with the header")


def f29(api):
    ann = person(api)
    small, big, busy = room(api, "Small", 2), room(api, "Big", 10), room(api, "Busy", 10)
    late, shut, tight, gone = room(api, "Late", 10), room(api, "Shut", 10), room(api, "Tight", 10, bufferMinutes=30), room(api, "Gone", 10)
    book(api, busy, ann, 4, "09:30", "10:30")
    api.put("/rooms/%s/hours" % late["id"], {"open": "12:00", "close": "18:00"})
    api.post("/rooms/%s/closures" % shut["id"], {"date": "2031-03-04"})
    book(api, tight, ann, 4, "08:00", "09:00")
    api.delete("/rooms/%s" % gone["id"])
    free = api.get("/rooms/available", start=T(4, "09:00"), end=T(4, "10:00"), attendees=4)
    check(ids(free) == ids([big]), "only the one large, free, open room should be available: %r" % [r.get("name") for r in free])
    check(free[0]["name"] == "Big" and free[0]["capacity"] == 10, "the answer is not the rooms themselves")
    anyone = api.get("/rooms/available", start=T(4, "09:00"), end=T(4, "10:00"))
    check(ids(anyone) == ids([small, big]), "without attendees the small room should be available too: %r" % [r.get("name") for r in anyone])
    afternoon = api.get("/rooms/available", start=T(4, "13:00"), end=T(4, "14:00"), attendees=4)
    check(ids(afternoon) == ids([big, busy, late, tight]), "the afternoon's free rooms are wrong: %r" % [r.get("name") for r in afternoon])
    api.get("/rooms/available", 400, start="soon", end=T(4, "10:00"))
    api.get("/rooms/available", 400, start=T(4, "10:00"), end=T(4, "09:00"))
    api.get("/rooms/available", 400, end=T(4, "10:00"))
    check(api.get("/rooms/%s" % big["id"])["name"] == "Big", "GET /rooms/:id stopped working")


def f30(api):
    blue, ann = room(api), person(api)
    made = book(api, blue, ann, 4)
    arrived = api.post("/bookings/%s/checkin" % made["id"], want=200)
    check(arrived["status"] == "checked_in" and booking(api, made)["status"] == "checked_in", "check-in did not set the status to checked_in")
    api.post("/bookings/%s/checkin" % made["id"], want=409)
    book(api, blue, ann, 4, "09:30", "10:30", want=409)
    api.post("/bookings/%s/cancel" % made["id"], want=409)
    api.patch("/bookings/%s" % made["id"], {"end": T(4, "11:00")}, 409)
    check(booking(api, made)["status"] == "checked_in", "a refused change altered a checked-in booking")
    dropped = book(api, blue, ann, 5)
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    api.post("/bookings/%s/checkin" % dropped["id"], want=409)
    api.post("/bookings/999999/checkin", want=404)


def f31(api):
    blue, ann = room(api, hourlyRate=40), person(api)
    made = book(api, blue, ann, 4)
    api.post("/bookings/%s/payments" % made["id"], {"amount": 30})
    missed = api.post("/bookings/%s/no-show" % made["id"], want=200)
    check(missed["status"] == "no_show" and booking(api, made)["status"] == "no_show", "no-show did not set the status to no_show")
    check(near(booking(api, made)["paid"], 30), "a no-show was refunded")
    api.post("/bookings/%s/no-show" % made["id"], want=409)
    book(api, blue, ann, 4)
    arrived = book(api, blue, ann, 5)
    api.post("/bookings/%s/checkin" % arrived["id"], want=200)
    api.post("/bookings/%s/no-show" % arrived["id"], want=409)
    api.post("/bookings/999999/no-show", want=404)
    summary = api.get("/customers/%s/summary" % ann["id"])
    check(summary["noShows"] == 1 and summary["confirmed"] == 1 and summary["cancelled"] == 0, "the summary's noShows or other counts are wrong: %r" % summary)


def f32(api):
    blue, ann = room(api, hourlyRate=40), person(api)
    made = book(api, blue, ann, 4)
    check([e["action"] for e in api.get("/bookings/%s/history" % made["id"])] == ["created"], "a new booking's history is not one created event")
    api.patch("/bookings/%s" % made["id"], {"start": T(4, "11:00"), "end": T(4, "12:00")})
    api.post("/bookings/%s/payments" % made["id"], {"amount": 10})
    api.post("/bookings/%s/cancel" % made["id"], want=200)
    events = api.get("/bookings/%s/history" % made["id"])
    check([e["action"] for e in events] == ["created", "rescheduled", "paid", "cancelled"], "the history is not created, rescheduled, paid, cancelled: %r" % events)
    times = [instant(e["at"]) for e in events]
    check(times == sorted(times) and times[0].year >= 2024, "the events' times are not when they happened, oldest first")
    arrived, missed = book(api, blue, ann, 5), book(api, blue, ann, 6)
    api.post("/bookings/%s/checkin" % arrived["id"], want=200)
    api.post("/bookings/%s/no-show" % missed["id"], want=200)
    check([e["action"] for e in api.get("/bookings/%s/history" % arrived["id"])] == ["created", "checked_in"], "check-in is not in the history")
    check([e["action"] for e in api.get("/bookings/%s/history" % missed["id"])] == ["created", "no_show"], "a no-show is not in the history")
    api.get("/bookings/999999/history", 404)


def f33(api):
    blue, green, ann = room(api, hourlyRate=40), room(api, "Green", hourlyRate=10), person(api)
    room(api, "Quiet")
    book(api, blue, ann, 4, "09:00", "11:00")
    arrived = book(api, blue, ann, 4, "12:00", "13:00")
    api.post("/bookings/%s/checkin" % arrived["id"], want=200)
    dropped = book(api, blue, ann, 4, "14:00", "15:00")
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    book(api, green, ann, 4, "09:00", "10:00")
    book(api, green, ann, 5, "09:00", "10:00")
    report = api.get("/reports/daily", date="2031-03-04")
    check(report["date"] == "2031-03-04" and report["bookings"] == 3 and near(report["revenue"], 130), "the day's bookings and revenue are wrong: %r" % report)
    rooms = {str(r["roomId"]): r for r in report["rooms"]}
    check(sorted(rooms) == sorted([str(blue["id"]), str(green["id"])]), "rooms is not one entry per room with a booking that day")
    check(rooms[str(blue["id"])]["bookings"] == 2 and near(rooms[str(blue["id"])]["revenue"], 120), "a room's entry is wrong: %r" % rooms[str(blue["id"])])
    check(rooms[str(green["id"])]["bookings"] == 1 and near(rooms[str(green["id"])]["revenue"], 10), "a room's entry is wrong: %r" % rooms[str(green["id"])])
    empty = api.get("/reports/daily", date="2031-03-20")
    check(empty["bookings"] == 0 and near(empty["revenue"], 0) and empty["rooms"] == [], "a day without bookings is not zeros and no rooms")
    api.get("/reports/daily", 400)
    api.get("/reports/daily", 400, date="yesterday")


def f34(api):
    blue, green, ann = room(api), room(api, "Green"), person(api)
    book(api, blue, ann, 3, "09:00", "11:00")
    book(api, blue, ann, 5, "22:00", "23:30")
    arrived = book(api, blue, ann, 4, "09:00", "10:00")
    api.post("/bookings/%s/checkin" % arrived["id"], want=200)
    dropped = book(api, blue, ann, 4, "12:00", "16:00")
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    book(api, blue, ann, 6, "09:00", "12:00")
    book(api, blue, ann, 2, "09:00", "12:00")
    used = {str(r["roomId"]): r["hours"] for r in api.get("/reports/utilization", **{"from": "2031-03-03", "to": "2031-03-05"})}
    check(sorted(used) == sorted([str(blue["id"]), str(green["id"])]), "the report is not one entry for every room")
    check(near(used[str(blue["id"])], 4.5) and near(used[str(green["id"])], 0), "the hours are wrong: %r" % used)
    one_day = {str(r["roomId"]): r["hours"] for r in api.get("/reports/utilization", **{"from": "2031-03-06", "to": "2031-03-06"})}
    check(near(one_day[str(blue["id"])], 3), "a one-day range does not count that day")
    api.get("/reports/utilization", 400, **{"from": "2031-03-05", "to": "2031-03-03"})
    api.get("/reports/utilization", 400, **{"from": "2031-03-05"})
    api.get("/reports/utilization", 400, **{"from": "start", "to": "2031-03-05"})


def f35(api):
    ann = person(api)
    both = room(api, "Both", 6, equipment=["projector", "whiteboard"])
    board, bare = room(api, "Board", 6, equipment=["whiteboard"]), room(api, "Bare", 6)
    check(both["equipment"] == ["projector", "whiteboard"] and bare["equipment"] == [], "the room does not show its equipment, empty when left out")
    check(ids(api.get("/rooms", equipment="whiteboard")) == ids([both, board]), "equipment does not filter GET /rooms")
    check(ids(api.get("/rooms", equipment="projector,whiteboard")) == ids([both]), "two items do not keep only rooms with both")
    check(api.get("/rooms", equipment="hot tub") == [], "an item no room has still returns rooms")
    check(len(api.get("/rooms")) == 3, "GET /rooms without the filter is not every room")
    api.post("/rooms", {"name": "Bad", "capacity": 2, "equipment": "projector"}, 400)
    api.patch("/rooms/%s" % bare["id"], {"equipment": [1, 2]}, 400)
    check(api.patch("/rooms/%s" % bare["id"], {"equipment": ["projector"]})["equipment"] == ["projector"], "PATCH did not set the equipment")
    check(api.patch("/rooms/%s" % bare["id"], {"name": "Bare 2"})["equipment"] == ["projector"], "changing the name lost the equipment")
    book(api, both, ann, 4)
    free = api.get("/rooms/available", start=T(4, "09:00"), end=T(4, "10:00"), equipment="projector")
    check(ids(free) == ids([bare]), "GET /rooms/available does not filter by equipment: %r" % [r.get("name") for r in free])


def f36(api):
    blue, ann = room(api, hourlyRate=40), person(api)
    one = book(api, blue, ann, 4, "09:00", "11:00")
    two = book(api, blue, ann, 5, "09:00", "09:30")
    api.post("/bookings/%s/cancel" % two["id"], want=200)
    sheet = api.raw("GET", "/bookings.csv", 200)
    check(sheet.headers.get("Content-Type", "").startswith("text/csv"), "the content type is not text/csv: %r" % sheet.headers.get("Content-Type"))
    lines = [line.strip() for line in sheet.raw.strip().splitlines()]
    check(lines[0] == "id,roomId,customerId,start,end,status,price", "the first line is not the header asked for: %r" % lines[0])
    check(len(lines) == 3, "there is not one line per booking: %r" % lines)
    rows = {line.split(",")[0].strip('"'): [cell.strip('"') for cell in line.split(",")] for line in lines[1:]}
    row = rows[str(one["id"])]
    check(len(row) == 7 and row[1] == str(blue["id"]) and row[2] == str(ann["id"]), "a line's room or customer is wrong: %r" % row)
    check(same(row[3], T(4, "09:00")) and same(row[4], T(4, "11:00")) and row[5] == "confirmed" and near(float(row[6]), 80), "a line's times, status or price are wrong: %r" % row)
    only = api.raw("GET", "/bookings.csv?status=cancelled", 200).raw.strip().splitlines()
    check(len(only) == 2 and only[1].split(",")[0].strip('"') == str(two["id"]), "the export does not take the filters of GET /bookings")
    check(len(api.get("/bookings")) == 2 and booking(api, one)["status"] == "confirmed", "GET /bookings or GET /bookings/:id stopped working")


def f37(api):
    blue, ann, bo = room(api, hourlyRate=20), person(api, 1), person(api, 2)
    closures = "/rooms/%s/closures" % blue["id"]
    paid = book(api, blue, ann, 9, "09:00", "10:00")
    api.post("/bookings/%s/payments" % paid["id"], {"amount": 20})
    late = api.post("/bookings", {"roomId": blue["id"], "customerId": bo["id"], "start": T(8, "23:00"), "end": T(9, "01:00")})
    next_day = book(api, blue, ann, 10)
    dropped = book(api, blue, ann, 9, "15:00", "16:00")
    api.post("/bookings/%s/cancel" % dropped["id"], want=200)
    api.post(closures, {"date": "2031-03-09"}, 409)
    check(api.get(closures) == [], "a refused closure was added")
    check(booking(api, paid)["status"] == "confirmed", "a refused closure cancelled a booking")
    shut = api.post(closures, {"date": "2031-03-09", "reason": "flood", "cancelBookings": True})
    check(sorted(str(i) for i in shut["cancelled"]) == ids([paid, late]), "cancelled is not the ids of the bookings that touched the day: %r" % shut.get("cancelled"))
    check(shut["date"] == "2031-03-09" and len(api.get(closures)) == 1, "the closure was not added")
    after = booking(api, paid)
    check(after["status"] == "cancelled" and near(after["paid"], 0), "a booking cancelled by the closure was not refunded: %r" % after)
    check(booking(api, late)["status"] == "cancelled" and booking(api, next_day)["status"] == "confirmed", "the wrong bookings were cancelled")
    empty = api.post(closures, {"date": "2031-03-12"})
    check(empty["date"] == "2031-03-12", "closing a day without bookings stopped working")
    book(api, blue, ann, 9, want=409)


def f38(api):
    blue, ann = room(api), person(api)
    body = slot(blue, ann, 4)
    first = api.post("/bookings", body, 201, headers={"Idempotency-Key": "key-one"})
    again = api.post("/bookings", body, 200, headers={"Idempotency-Key": "key-one"})
    check(ident(again["id"], first["id"]) and same(again["start"], T(4)), "the repeated request did not answer with the first booking")
    check(len(api.get("/bookings")) == 1, "the repeated request made a second booking")
    api.post("/bookings", body, 409, headers={"Idempotency-Key": "key-two"})
    other = api.post("/bookings", slot(blue, ann, 5), 201, headers={"Idempotency-Key": "key-three"})
    check(not ident(other["id"], first["id"]), "a different key answered with the first booking")
    api.post("/bookings", slot(blue, ann, 6), 201)
    api.post("/bookings", slot(blue, ann, 6), 409)
    check(ident(api.post("/bookings", body, 200, headers={"Idempotency-Key": "key-one"})["id"], first["id"]), "a key does not keep answering with its booking")
    check(len(api.get("/bookings")) == 3, "the bookings made are not the three that should exist")


def f39(api):
    ann = person(api)
    cheap, dear, tiny, taken = room(api, "Cheap", 10, hourlyRate=10), room(api, "Dear", 10, hourlyRate=30), room(api, "Tiny", 1), room(api, "Taken", 10)
    api.post("/coupons", {"code": "HALF", "percentOff": 50})
    made = book(api, cheap, ann, 4, "09:00", "11:00", attendees=2)
    book(api, taken, ann, 4, "10:00", "12:00")
    moved = api.post("/bookings/%s/move" % made["id"], {"roomId": dear["id"]}, 200)
    check(ident(moved["roomId"], dear["id"]) and same(moved["start"], T(4, "09:00")) and same(moved["end"], T(4, "11:00")), "the booking is not in the new room at the same time")
    check(near(moved["price"], 60) and ident(booking(api, made)["roomId"], dear["id"]), "the moved booking is not priced at the new room's rate, or the move was not kept")
    book(api, cheap, ann, 4, "09:00", "11:00")
    api.post("/bookings/%s/move" % made["id"], {"roomId": tiny["id"]}, 400)
    api.post("/bookings/%s/move" % made["id"], {"roomId": taken["id"]}, 409)
    api.post("/bookings/%s/move" % made["id"], {"roomId": 999999}, 404)
    api.post("/bookings/999999/move", {"roomId": dear["id"]}, 404)
    check(ident(booking(api, made)["roomId"], dear["id"]), "a refused move changed the booking")
    cut = book(api, cheap, ann, 5, "09:00", "11:00", couponCode="HALF")
    over = api.post("/bookings/%s/move" % cut["id"], {"roomId": dear["id"]}, 200)
    check(near(over["price"], 30) and near(over["discount"], 30) and over["couponCode"] == "HALF", "a moved booking lost or miscounted its coupon: %r" % over)
    api.post("/bookings/%s/cancel" % cut["id"], want=200)
    api.post("/bookings/%s/move" % cut["id"], {"roomId": cheap["id"]}, 409)


def f40(api):
    blue, ann, bo = room(api, hourlyRate=10), person(api, 1), person(api, 2)
    waitlist = "/rooms/%s/waitlist" % blue["id"]
    held = book(api, blue, ann, 4)
    api.post("/bookings/%s/payments" % held["id"], {"amount": 10})
    old = book(api, blue, ann, 5)
    api.post("/bookings/%s/cancel" % old["id"], want=200)
    book(api, blue, bo, 6)
    api.post(waitlist, {"customerId": ann["id"], "start": T(6), "end": T(6, "10:00")})
    api.post(waitlist, {"customerId": bo["id"], "start": T(4), "end": T(4, "10:00")})
    api.delete("/customers/%s" % ann["id"])
    api.get("/customers/%s" % ann["id"], 404)
    check(ids(api.get("/customers")) == ids([bo]), "the removed customer is still listed")
    after = booking(api, held)
    check(after["status"] == "cancelled" and near(after["paid"], 0), "the removed customer's booking was not cancelled and refunded: %r" % after)
    check(booking(api, old)["status"] == "cancelled", "the removed customer's old booking is no longer visible as cancelled")
    promoted = [b for b in api.get("/bookings", customerId=bo["id"]) if same(b["start"], T(4)) and b["status"] == "confirmed"]
    check(len(promoted) == 1, "the cancellation did not book the next customer on the waiting list")
    check(api.get(waitlist) == [], "the removed customer's waiting-list entry is still there")
    api.delete("/customers/%s" % ann["id"], 404)
    api.delete("/customers/999999", 404)
    reborn = api.post("/customers", {"name": "New Ann", "email": "person1@example.com"})
    check(not ident(reborn["id"], ann["id"]), "the email could be reused only by reviving the removed customer")


TESTS = {int(name[1:]): test for name, test in list(globals().items()) if name[0] == "f" and name[1:].isdigit()}
MISSHAPEN = (KeyError, TypeError, IndexError, AttributeError, ValueError)


def one(tree, number):
    """{ok, said, calls}: the feature's test against a fresh start of the tree's app."""
    try:
        started = runner.App(tree)
    except runner.Down as error:
        return {"ok": False, "said": str(error), "calls": []}
    api = Api(started)
    try:
        TESTS[number](api)
        return {"ok": True, "said": "", "calls": api.calls}
    except Wrong as error:
        return {"ok": False, "said": str(error), "calls": api.calls}
    except runner.Down as error:
        return {"ok": False, "said": "the app stopped answering: %s" % error, "calls": api.calls}
    except MISSHAPEN as error:
        return {"ok": False, "said": "an answer was not shaped as asked: %r" % error, "calls": api.calls}
    finally:
        started.stop()


def run(tree, upto=None, jobs=4):
    numbers = [n for n in sorted(TESTS) if upto is None or n <= upto]
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        return dict(zip(numbers, pool.map(lambda n: one(tree, n), numbers)))


if __name__ == "__main__":
    results = run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
    for number, result in results.items():
        print("%2d  %s  %s" % (number, "pass" if result["ok"] else "FAIL", result["said"]))
    print("%d of %d pass" % (sum(r["ok"] for r in results.values()), len(results)))
    sys.exit(0 if all(r["ok"] for r in results.values()) else 1)
