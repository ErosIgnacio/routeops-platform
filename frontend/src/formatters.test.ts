import { describe, expect, it } from "vitest";

import { formatEstimatedCost } from "./formatters";

describe("formatEstimatedCost", () => {
  it("labels Chilean pesos explicitly", () => {
    expect(formatEstimatedCost(47660.44, "CLP")).toBe("CLP $47.660");
  });
});
