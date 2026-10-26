"""templates — the clean project per language that every seed is injected into.

Layout (layering root `src`, a layer per top-level directory): core (reaches nothing),
models (reaches core), services (reaches models and core), and the entry file at src/ (App,
reaches anything). Every declared symbol is referenced, there is no escape, no clone and no
function over the ceilings, so the attached gates are green before any seed.
"""

T = {}

T["python"] = {
    "src/core/__init__.py": "",
    "src/core/stats.py": '''"""Small statistics helpers."""


def summarize(values):
    total = 0
    count = 0
    smallest = None
    largest = None
    for value in values:
        total += value
        count += 1
        if smallest is None or value < smallest:
            smallest = value
        if largest is None or value > largest:
            largest = value
    mean = total / count if count else 0
    return {"total": total, "count": count, "min": smallest, "max": largest, "mean": mean}


def classify(score):
    if score > 90:
        return "high"
    if score > 50:
        return "medium"
    return "low"
''',
    "src/models/__init__.py": "",
    "src/models/item.py": '''from core.stats import classify


class Item:
    def __init__(self, name, price):
        self.name = name
        self.price = price

    def grade(self):
        return classify(self.price)
''',
    "src/services/__init__.py": "",
    "src/services/billing.py": '''from models.item import Item
from core.stats import summarize


def charge(items):
    prices = [item.price for item in items]
    return summarize(prices)


def make_item(name, price):
    return Item(name, price)
''',
    "src/main.py": '''from services.billing import charge, make_item


def main():
    items = [make_item("a", 3), make_item("b", 5)]
    print(charge(items))
    print(items[0].grade())


if __name__ == "__main__":
    main()
''',
}

T["typescript"] = {
    "src/core/stats.ts": '''export interface Summary {
  total: number;
  count: number;
  largest: number;
  mean: number;
}

export function summarize(values: number[]): Summary {
  let total = 0;
  let count = 0;
  let largest = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    total += value;
    count += 1;
    if (value > largest) {
      largest = value;
    }
  }
  const mean = count > 0 ? total / count : 0;
  return { total: total, count: count, largest: largest, mean: mean };
}

export function classify(score: number): string {
  if (score > 90) {
    return "high";
  }
  if (score > 50) {
    return "medium";
  }
  return "low";
}
''',
    "src/core/format.js": '''export function formatPrice(price) {
  return "$" + price.toFixed(2);
}
''',
    "src/models/item.ts": '''import { classify } from "../core/stats";

export class Item {
  constructor(
    public readonly name: string,
    public readonly price: number,
  ) {}

  grade(): string {
    return classify(this.price);
  }
}
''',
    "src/services/billing.ts": '''import { Item } from "../models/item";
import { summarize, Summary } from "../core/stats";

export function charge(items: Item[]): Summary {
  const prices = items.map((item) => item.price);
  return summarize(prices);
}

export function makeItem(name: string, price: number): Item {
  return new Item(name, price);
}
''',
    "src/main.ts": '''import { charge, makeItem } from "./services/billing";
import { formatPrice } from "./core/format.js";

export function main(): void {
  const items = [makeItem("a", 3), makeItem("b", 5)];
  console.log(charge(items));
  console.log(items[0].grade(), formatPrice(items[0].price));
}

main();
''',
}

T["swift"] = {
    "src/core/Stats.swift": '''struct Summary {
    let total: Int
    let count: Int
    let largest: Int
}

func summarize(_ values: [Int]) -> Summary {
    var total = 0
    var count = 0
    var largest = Int.min
    for value in values {
        total += value
        count += 1
        if value > largest {
            largest = value
        }
    }
    let result = Summary(total: total, count: count, largest: largest)
    return result
}

func classify(_ score: Int) -> String {
    if score > 90 {
        return "high"
    }
    if score > 50 {
        return "medium"
    }
    return "low"
}
''',
    "src/models/Item.swift": '''struct Item {
    let name: String
    let price: Int

    func grade() -> String {
        return classify(price)
    }
}
''',
    "src/services/BillingService.swift": '''struct BillingService {
    func charge(_ items: [Item]) -> Summary {
        let prices = items.map { $0.price }
        return summarize(prices)
    }

    func makeItem(_ name: String, _ price: Int) -> Item {
        return Item(name: name, price: price)
    }
}

func chargeAll(_ items: [Item]) -> Summary {
    return BillingService().charge(items)
}
''',
    "src/main.swift": '''let service = BillingService()
let items = [service.makeItem("a", 3), service.makeItem("b", 5)]
print(service.charge(items).total, chargeAll(items).count)
print(items[0].grade())
''',
}

T["rust"] = {
    "Cargo.toml": '''[package]
name = "acme"
version = "0.1.0"
edition = "2021"
''',
    "src/lib.rs": '''pub mod base;
pub mod models;
pub mod services;

pub fn run_demo() -> i64 {
    let items = vec![services::billing::make_item("a", 3), services::billing::make_item("b", 5)];
    let summary = services::billing::charge(&items);
    let first_grade = items[0].grade();
    summary.total + first_grade.len() as i64
}
''',
    "src/base/mod.rs": "pub mod stats;\n",
    "src/base/stats.rs": '''pub struct Summary {
    pub total: i64,
    pub count: i64,
    pub largest: i64,
}

pub fn summarize(values: &[i64]) -> Summary {
    let mut total = 0;
    let mut count = 0;
    let mut largest = i64::MIN;
    for value in values {
        total += value;
        count += 1;
        if *value > largest {
            largest = *value;
        }
    }
    Summary { total: total, count: count, largest: largest }
}

pub fn classify(score: i64) -> &'static str {
    if score > 90 {
        return "high";
    }
    if score > 50 {
        return "medium";
    }
    "low"
}
''',
    "src/models/mod.rs": "pub mod item;\n",
    "src/models/item.rs": '''use crate::base::stats::classify;

pub struct Item {
    pub name: String,
    pub price: i64,
}

impl Item {
    pub fn grade(&self) -> &'static str {
        classify(self.price)
    }
}
''',
    "src/services/mod.rs": "pub mod billing;\n",
    "src/services/billing.rs": '''use crate::base::stats::{summarize, Summary};
use crate::models::item::Item;

pub fn charge(items: &[Item]) -> Summary {
    let prices: Vec<i64> = items.iter().map(|item| item.price).collect();
    summarize(&prices)
}

pub fn make_item(name: &str, price: i64) -> Item {
    Item { name: name.to_string(), price }
}
''',
}

T["go"] = {
    "go.mod": "module example.com/acme\n\ngo 1.22\n",
    "src/core/stats.go": '''package core

type Summary struct {
	Total   int
	Count   int
	Largest int
}

func Summarize(values []int) Summary {
	total := 0
	count := 0
	largest := 0
	for _, value := range values {
		total += value
		count++
		if value > largest {
			largest = value
		}
	}
	return Summary{Total: total, Count: count, Largest: largest}
}

func Classify(score int) string {
	if score > 90 {
		return "high"
	}
	if score > 50 {
		return "medium"
	}
	return "low"
}
''',
    "src/models/item.go": '''package models

import "example.com/acme/src/core"

type Item struct {
	Name  string
	Price int
}

func (i Item) Grade() string {
	return core.Classify(i.Price)
}
''',
    "src/services/billing.go": '''package services

import (
	"example.com/acme/src/core"
	"example.com/acme/src/models"
)

func Charge(items []models.Item) core.Summary {
	prices := make([]int, 0, len(items))
	for _, item := range items {
		prices = append(prices, item.Price)
	}
	return core.Summarize(prices)
}

func MakeItem(name string, price int) models.Item {
	return models.Item{Name: name, Price: price}
}
''',
    "src/main.go": '''package main

import (
	"fmt"

	"example.com/acme/src/models"
	"example.com/acme/src/services"
)

func main() {
	items := []models.Item{services.MakeItem("a", 3), services.MakeItem("b", 5)}
	fmt.Println(services.Charge(items), items[0].Grade())
}
''',
}

T["kotlin"] = {
    "src/core/Stats.kt": '''package com.acme.core

data class Summary(val total: Int, val count: Int, val largest: Int)

fun summarize(values: List<Int>): Summary {
    var total = 0
    var count = 0
    var largest = Int.MIN_VALUE
    for (value in values) {
        total += value
        count += 1
        if (value > largest) {
            largest = value
        }
    }
    return Summary(total = total, count = count, largest = largest)
}

fun classify(score: Int): String {
    if (score > 90) {
        return "high"
    }
    if (score > 50) {
        return "medium"
    }
    return "low"
}
''',
    "src/models/Item.kt": '''package com.acme.models

import com.acme.core.classify

data class Item(val name: String, val price: Int) {
    fun grade(): String = classify(price)
}
''',
    "src/services/Billing.kt": '''package com.acme.services

import com.acme.core.Summary
import com.acme.core.summarize
import com.acme.models.Item

class Billing {
    fun charge(items: List<Item>): Summary = summarize(items.map { it.price })

    fun makeItem(name: String, price: Int): Item = Item(name, price)
}
''',
    "src/Main.kt": '''package com.acme

import com.acme.services.Billing

fun main() {
    val billing = Billing()
    val items = listOf(billing.makeItem("a", 3), billing.makeItem("b", 5))
    println(billing.charge(items))
    println(items[0].grade())
}
''',
}

T["java"] = {
    "src/core/Stats.java": '''package com.acme.core;

import java.util.List;

public final class Stats {
    private Stats() {}

    public static int[] summarize(List<Integer> values) {
        int total = 0;
        int count = 0;
        int largest = Integer.MIN_VALUE;
        for (int value : values) {
            total += value;
            count += 1;
            if (value > largest) {
                largest = value;
            }
        }
        int[] result = {total, count, largest};
        return result;
    }

    public static String classify(int score) {
        if (score > 90) {
            return "high";
        }
        if (score > 50) {
            return "medium";
        }
        return "low";
    }
}
''',
    "src/models/Item.java": '''package com.acme.models;

import com.acme.core.Stats;

public final class Item {
    private final String name;
    private final int price;

    public Item(String name, int price) {
        this.name = name;
        this.price = price;
    }

    public int getPrice() {
        return price;
    }

    public String getName() {
        return name;
    }

    public String grade() {
        return Stats.classify(price);
    }
}
''',
    "src/services/Billing.java": '''package com.acme.services;

import com.acme.core.Stats;
import com.acme.models.Item;
import java.util.ArrayList;
import java.util.List;

public final class Billing {
    public int[] charge(List<Item> items) {
        List<Integer> prices = new ArrayList<>();
        for (Item item : items) {
            prices.add(item.getPrice());
        }
        return Stats.summarize(prices);
    }

    public Item makeItem(String name, int price) {
        return new Item(name, price);
    }
}
''',
    "src/Main.java": '''package com.acme;

import com.acme.services.Billing;
import java.util.List;

public final class Main {
    public static void main(String[] args) {
        Billing billing = new Billing();
        var items = List.of(billing.makeItem("a", 3), billing.makeItem("b", 5));
        System.out.println(billing.charge(items)[0] + " " + items.get(0).grade() + items.get(0).getName());
    }
}
''',
}

T["ruby"] = {
    "src/core/stats.rb": '''module Stats
  def self.summarize(values)
    total = 0
    count = 0
    largest = nil
    values.each do |value|
      total += value
      count += 1
      largest = value if largest.nil? || value > largest
    end
    mean = count.zero? ? 0 : total.fdiv(count)
    { total: total, count: count, largest: largest, mean: mean }
  end

  def self.classify(score)
    return "high" if score > 90
    return "medium" if score > 50

    "low"
  end
end
''',
    "src/models/item.rb": '''require_relative "../core/stats"

class Item
  attr_reader :name, :price

  def initialize(name, price)
    @name = name
    @price = price
  end

  def grade
    Stats.classify(price)
  end
end
''',
    "src/services/billing.rb": '''require_relative "../models/item"
require_relative "../core/stats"

module Billing
  def self.charge(items)
    Stats.summarize(items.map(&:price))
  end

  def self.make_item(name, price)
    Item.new(name, price)
  end
end
''',
    "src/main.rb": '''require_relative "services/billing"

items = [Billing.make_item("a", 3), Billing.make_item("b", 5)]
puts Billing.charge(items)
puts items.first.grade
''',
}

T["shell"] = {
    "src/lib.sh": '''#!/usr/bin/env bash
set -euo pipefail

summarize() {
  local total=0
  local count=0
  local largest=0
  for value in "$@"; do
    total=$((total + value))
    count=$((count + 1))
    if [ "$value" -gt "$largest" ]; then
      largest=$value
    fi
  done
  echo "total=$total count=$count largest=$largest"
}

classify() {
  if [ "$1" -gt 90 ]; then
    echo high
  elif [ "$1" -gt 50 ]; then
    echo medium
  else
    echo low
  fi
}
''',
    "src/main.sh": '''#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"
summarize 3 5 8
classify 72
''',
}

# Where the summarize function lives, and its text, per language (the duplication source).
STATS_FILE = {"python": "src/core/stats.py", "typescript": "src/core/stats.ts", "swift": "src/core/Stats.swift",
              "rust": "src/base/stats.rs", "go": "src/core/stats.go", "kotlin": "src/core/Stats.kt",
              "java": "src/core/Stats.java", "ruby": "src/core/stats.rb", "shell": "src/lib.sh"}
MODEL_FILE = {"python": "src/models/item.py", "typescript": "src/models/item.ts", "swift": "src/models/Item.swift",
              "rust": "src/models/item.rs", "go": "src/models/item.go", "kotlin": "src/models/Item.kt",
              "java": "src/models/Item.java", "ruby": "src/models/item.rb", "shell": "src/main.sh"}
EXT = {"python": ".py", "typescript": ".ts", "swift": ".swift", "rust": ".rs", "go": ".go", "kotlin": ".kt",
       "java": ".java", "ruby": ".rb", "shell": ".sh"}
