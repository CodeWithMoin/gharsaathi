import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { addressGate, addressLabel, deliveringLine, headerAddress, orderCardLinks } from "../../app/home-header.mjs";

const home = { id: "z1", address_line: "Flat 402, Prestige Shantiniketan, Whitefield Main Road, Bengaluru 560048", tag: "home" };
const office = { id: "z2", address_line: "Embassy Tech Village, Outer Ring Road", tag: "Office" };
const store = (overrides = {}) => ({ connected: true, addresses: [home, office], selectedId: "", loaded: true, ...overrides });
const off = { connected: false, addresses: [], selectedId: "", loaded: false };

test("no address chosen → 'Where should we deliver?' comes first, for the connected store", () => {
  const gate = addressGate({ zepto: store(), swiggy: off });
  assert.equal(gate?.store, "zepto");
  assert.equal(gate?.addresses.length, 2);
  assert.equal(gate?.canAdd, true);
  assert.equal(addressGate({ zepto: off, swiggy: store() })?.canAdd, false, "Swiggy addresses can't be added here");
});

test("a selected address that no longer exists counts as not chosen", () => {
  assert.ok(addressGate({ zepto: store({ selectedId: "gone" }), swiggy: off }));
  assert.equal(headerAddress({ zepto: store({ selectedId: "gone" }), swiggy: off }).state, "choose");
});

test("address selected → no picker; header and greeting show the label", () => {
  const stores = { zepto: store({ selectedId: "z1" }), swiggy: off };
  assert.equal(addressGate(stores), null);
  const header = headerAddress(stores);
  assert.deepEqual([header.state, header.store, header.label], ["selected", "zepto", "Home"]);
  assert.equal(header.line, home.address_line);
  assert.equal(deliveringLine(header), "Delivering to Home");
  assert.match(header.accessibleLabel, /Delivering to Home/);
});

test("either store with a valid address is enough; Zepto is preferred in the header", () => {
  const swiggyOnly = { zepto: store(), swiggy: store({ addresses: [{ id: "s1", address_line: "MG Road", category: "Work" }], selectedId: "s1" }) };
  assert.equal(addressGate(swiggyOnly), null);
  assert.equal(headerAddress(swiggyOnly).label, "Work");
  const both = { zepto: store({ selectedId: "z2" }), swiggy: swiggyOnly.swiggy };
  assert.equal(headerAddress(both).label, "Office");
});

test("never blocks while loading or with no store connected", () => {
  assert.equal(addressGate({ zepto: store({ loaded: false }), swiggy: off }), null);
  assert.equal(addressGate({ zepto: off, swiggy: off }), null);
  assert.equal(headerAddress({ zepto: { ...off, connected: null }, swiggy: off }).state, "checking");
  const none = headerAddress({ zepto: off, swiggy: off });
  assert.deepEqual([none.state, none.store, none.label], ["connect", null, "Connect a store"]);
});

test("labels: tag, then category, then a plain fallback", () => {
  assert.equal(addressLabel({ tag: "home" }), "Home");
  assert.equal(addressLabel({ category: "other" }), "Other");
  assert.equal(addressLabel({}), "Saved address");
});

test("long labels truncate in the header instead of pushing the avatar off at 360 px", () => {
  const header = readFileSync(new URL("../../app/home-header-bar.tsx", import.meta.url), "utf8");
  assert.match(header, /min-w-0 flex-1/);
  assert.match(header, /truncate text-\[15px\] font-bold/);
  assert.match(header, /block truncate text-xs/);
  assert.match(header, /<nav className="flex shrink-0/);
  assert.match(header, /const iconButton = "grid size-11 shrink-0/, "44px chats and basket buttons");
  assert.ok(header.indexOf("orderChip && <div") > header.indexOf("</nav>"), "order chip gets its own row");
  assert.ok(header.indexOf("<AccountMenuButton") > header.indexOf("Open basket"), "avatar sits right of the basket");
  assert.ok(header.indexOf("Open shopping chats") < header.indexOf('data-testid="header-address"'), "☰ stays at the far left");
  assert.ok(!header.includes("<Storefront"), "store icon replaced by the avatar");
});

test("order card links: delivery line + directions while moving, help always", () => {
  assert.deepEqual(orderCardLinks({ final: false, deliveryLineReady: true }).map((link) => link.label), ["Delivery line ready", "Directions", "Need help?"]);
  assert.deepEqual(orderCardLinks({ final: false, deliveryLineReady: false }).map((link) => link.label), ["Set up delivery line", "Directions", "Need help?"]);
  assert.deepEqual(orderCardLinks({ final: true, deliveryLineReady: true }).map((link) => link.id), ["help"]);
});

test("the order card wires Need help? to the support flow", () => {
  const page = readFileSync(new URL("../../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /<OrderStatusCard [^\n]*onOpenSupport=\{openSupport\}/);
  assert.match(page, /<OrderStatusCard [^\n]*onOpenDeliveryLine=\{\(\) => openSettingsSection\("delivery"\)\}/);
});
