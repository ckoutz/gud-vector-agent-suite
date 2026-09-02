import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applySmsTurn,
  emptySmsDraft,
  parseSmsQuote,
  quoteSentReply,
  smsAskPrompt,
} from "./sms-parse";

test("parses the natural lawn care text without commas", () => {
  const parsed = parseSmsQuote(
    "Cameron koutz lawn care $160/month 9258588301",
  );
  assert.ok(parsed);
  assert.equal(parsed.name, "Cameron Koutz");
  assert.equal(parsed.service, "lawn care");
  assert.equal(parsed.amountCents, 16000);
  assert.equal(parsed.billing, "monthly");
  assert.equal(parsed.phone, "+19258588301");
  assert.equal(parsed.email, null);
});

test("still parses the old comma format", () => {
  const parsed = parseSmsQuote(
    "Glen, 9255551234, glen@protech-cal.com, Website + systems setup, $1500",
  );
  assert.ok(parsed);
  assert.equal(parsed.name, "Glen");
  assert.equal(parsed.phone, "+19255551234");
  assert.equal(parsed.email, "glen@protech-cal.com");
  assert.equal(parsed.service.toLowerCase(), "website + systems setup");
  assert.equal(parsed.amountCents, 150000);
  assert.equal(parsed.billing, "one_time");
});

test("parses garden care monthly without email", () => {
  const parsed = parseSmsQuote("Maya, 9255559999, Garden care, $160/month");
  assert.ok(parsed);
  assert.equal(parsed.name, "Maya");
  assert.equal(parsed.service.toLowerCase(), "garden care");
  assert.equal(parsed.billing, "monthly");
  assert.equal(parsed.amountCents, 16000);
});

test("asks only for the missing phone, then completes on follow-up", () => {
  const first = applySmsTurn(
    emptySmsDraft(),
    "Cameron koutz lawn care $160/month",
  );
  assert.equal(first.complete, null);
  assert.match(first.prompt ?? "", /phone number/i);
  assert.equal(first.draft.askedFor, "phone");

  const second = applySmsTurn(first.draft, "9258588301");
  assert.ok(second.complete);
  assert.equal(second.complete.name, "Cameron Koutz");
  assert.equal(second.complete.phone, "+19258588301");
  assert.equal(second.complete.service, "lawn care");
  assert.equal(second.complete.amountCents, 16000);
});

test("follow-up filler does not overwrite the customer name", () => {
  const first = applySmsTurn(
    emptySmsDraft(),
    "Cameron koutz lawn care $160/month",
  );
  const second = applySmsTurn(first.draft, "their number is 9255551234");
  assert.ok(second.complete);
  assert.equal(second.complete.name, "Cameron Koutz");
  assert.equal(second.complete.phone, "+19255551234");
});

test("does not dump the old comma format example", () => {
  const result = applySmsTurn(emptySmsDraft(), "lawn care $160/month");
  assert.equal(result.complete, null);
  assert.doesNotMatch(result.prompt ?? "", /Send:/);
  assert.doesNotMatch(result.prompt ?? "", /optional email/);
  assert.match(result.prompt ?? "", /name/i);
});

test("does not require an address to complete a quote", () => {
  const parsed = parseSmsQuote(
    "Cameron koutz lawn care $160/month 9258588301",
  );
  assert.ok(parsed);
  assert.equal(parsed.address, null);
});

test("admin confirmation does not include a format example", () => {
  const parsed = parseSmsQuote(
    "Cameron koutz lawn care $160/month 9258588301",
  );
  assert.ok(parsed);
  const reply = quoteSentReply(parsed);
  assert.match(reply, /Quote sent to Cameron Koutz/i);
  assert.match(reply, /\$160\/month/);
  assert.doesNotMatch(reply, /Send:/);
});

test("address prompt is available when we actually need it", () => {
  assert.match(
    smsAskPrompt("address", { service: "lawn care" }),
    /For lawn care I just need the service address/i,
  );
});
