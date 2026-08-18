import { describe, expect, it } from "vitest";

import {
  asLocation,
  defaultView,
  formatLocation,
  hasPin,
  locationLabel,
  mapHref,
  parseLocation,
} from "./location";
import type { Column } from "./types";

function column(config: Record<string, unknown> = {}): Column {
  return {
    uuid: "c1",
    name: "site",
    display_name: "Site",
    type: "location",
    config,
  } as Column;
}

const PLACE = { address: "12 Rizal St, Cebu City", lat: 10.3157, lng: 123.8854 };

describe("asLocation", () => {
  it("reads a cell", () => {
    expect(asLocation(PLACE)).toEqual(PLACE);
    expect(asLocation({ address: "Cebu City" })).toEqual({
      address: "Cebu City",
      lat: null,
      lng: null,
    });
  });

  it("reads a string too, because a location column can come from `text`", () => {
    // Until the conversion's apply runs, the cells are still lines of text that the
    // picker and the grid have to render as something.
    expect(asLocation("Cebu City @10.31570,123.88540")).toEqual({
      address: "Cebu City",
      lat: 10.3157,
      lng: 123.8854,
    });
  });

  it("is null for anything that is not a place", () => {
    expect(asLocation(null)).toBeNull();
    expect(asLocation({})).toBeNull();
    expect(asLocation({ address: null, lat: null, lng: null })).toBeNull();
    expect(asLocation(42)).toBeNull();
  });
});

describe("formatLocation / parseLocation", () => {
  it.each([
    [PLACE, "12 Rizal St, Cebu City @10.31570,123.88540"],
    [{ address: null, lat: 10.3157, lng: 123.8854 }, "@10.31570,123.88540"],
    [{ address: "Cebu City", lat: null, lng: null }, "Cebu City"],
  ])("writes %o as %s", (value, written) => {
    expect(formatLocation(value)).toBe(written);
  });

  it.each([
    ["12 Rizal St @10.31570,123.88540", { address: "12 Rizal St", lat: 10.3157, lng: 123.8854 }],
    ["@10.31570,123.88540", { address: null, lat: 10.3157, lng: 123.8854 }],
    // No leading space needed to *read* one, though one is written.
    ["12 Rizal St@10.0,123.0", { address: "12 Rizal St", lat: 10, lng: 123 }],
    ["12 Rizal St", { address: "12 Rizal St", lat: null, lng: null }],
    // An "@" that belongs to the address is not a coordinate suffix.
    ["Unit 3 @ The Mall", { address: "Unit 3 @ The Mall", lat: null, lng: null }],
    // Anchored at the end, so an address containing "@" survives the round trip.
    [
      "Unit 3 @ The Mall @10.00000,123.00000",
      { address: "Unit 3 @ The Mall", lat: 10, lng: 123 },
    ],
  ])("reads %s back", (written, value) => {
    expect(parseLocation(written)).toEqual(value);
  });

  it("is empty for an empty line", () => {
    expect(parseLocation("")).toBeNull();
    expect(parseLocation("   ")).toBeNull();
    expect(formatLocation(null)).toBe("");
  });

  it("round-trips", () => {
    for (const value of [
      PLACE,
      { address: null, lat: 10.3157, lng: 123.8854 },
      { address: "Cebu City", lat: null, lng: null },
      { address: "Unit 3 @ The Mall", lat: 10, lng: 123 },
    ]) {
      expect(parseLocation(formatLocation(value))).toEqual(value);
    }
  });
});

describe("hasPin", () => {
  it("is about the coordinates, not the address", () => {
    expect(hasPin(asLocation(PLACE))).toBe(true);
    expect(hasPin(asLocation({ address: "Cebu City" }))).toBe(false);
    expect(hasPin(null)).toBe(false);
  });
});

describe("locationLabel", () => {
  it("prefers the address, and falls back to the coordinates", () => {
    expect(locationLabel(PLACE)).toBe("12 Rizal St, Cebu City");
    expect(locationLabel({ address: null, lat: 10.3157, lng: 123.8854 })).toBe(
      "10.31570, 123.88540",
    );
  });

  it("never renders the raw object", () => {
    // Which is the whole reason this module exists: `String(cell)` is
    // "[object Object]", and this is the one cell shape that is an object.
    expect(locationLabel(PLACE)).not.toContain("object");
    expect(locationLabel({})).toBe("");
  });
});

describe("mapHref", () => {
  it("prefers the pin over the address, because a pin is unambiguous", () => {
    // An address string is a search, and a search can land on the wrong continent.
    expect(mapHref(column(), PLACE)).toContain("mlat=10.3157");
    expect(mapHref(column({ map_link: "google" }), PLACE)).toBe(
      "https://www.google.com/maps?q=10.3157,123.8854",
    );
  });

  it("searches the address when there is no pin", () => {
    const href = mapHref(column({ map_link: "google" }), { address: "Cebu City" });
    expect(href).toBe("https://www.google.com/maps?q=Cebu%20City");
  });

  it("is null when there is nothing to point at", () => {
    expect(mapHref(column(), null)).toBeNull();
    expect(mapHref(column(), {})).toBeNull();
  });
});

describe("defaultView", () => {
  it("opens on the world when the column names nowhere", () => {
    // Honest about knowing nothing, rather than dropping somebody into a city
    // picked by whoever wrote this file.
    expect(defaultView(column())).toEqual({ lat: 0, lng: 0, zoom: 2 });
  });

  it("uses the column's centre, and a street-level zoom with it", () => {
    expect(defaultView(column({ default_center: { lat: 10.3, lng: 123.9 } }))).toEqual({
      lat: 10.3,
      lng: 123.9,
      zoom: 13,
    });
    expect(
      defaultView(column({ default_center: { lat: 10.3, lng: 123.9 }, default_zoom: 17 })),
    ).toEqual({ lat: 10.3, lng: 123.9, zoom: 17 });
  });
});
