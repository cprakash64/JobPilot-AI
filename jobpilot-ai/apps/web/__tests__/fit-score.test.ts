import { describe, expect, it } from "vitest";
import { FIT_SCORE_STYLES, getFitScoreTone } from "../lib/fitScore";

describe("getFitScoreTone", () => {
  it("returns green emerald Strong fit for >= 80", () => {
    const tone = getFitScoreTone(88);
    expect(tone.key).toBe("strong");
    expect(tone.label).toBe("Strong fit");
    expect(tone.container).toContain("bg-emerald-50");
    expect(tone.number).toContain("text-emerald-700");
  });

  it("returns lime / yellow-green Good fit for 60–79", () => {
    const tone = getFitScoreTone(79);
    expect(tone.key).toBe("good");
    expect(tone.label).toBe("Good fit");
    expect(tone.container).toContain("bg-lime-50");
    expect(tone.number).toContain("text-lime-700");
    // Yellow-green, not a red-leaning palette.
    expect(tone.container).not.toContain("red");
  });

  it("returns warning orange Stretch for 45–59", () => {
    const tone = getFitScoreTone(55);
    expect(tone.key).toBe("stretch");
    expect(tone.label).toBe("Stretch");
    expect(tone.container).toContain("bg-orange-50");
    expect(tone.number).toContain("text-orange-700");
  });

  it("returns red Low fit for < 45", () => {
    const tone = getFitScoreTone(30);
    expect(tone.key).toBe("low");
    expect(tone.label).toBe("Low fit");
    expect(tone.container).toContain("bg-red-50");
    expect(tone.number).toContain("text-red-700");
  });

  it("handles boundary values inclusively", () => {
    expect(getFitScoreTone(80).key).toBe("strong");
    expect(getFitScoreTone(60).key).toBe("good");
    expect(getFitScoreTone(45).key).toBe("stretch");
    expect(getFitScoreTone(44).key).toBe("low");
  });

  it("returns a neutral tone when the score is missing", () => {
    expect(getFitScoreTone(null).key).toBe("none");
    expect(getFitScoreTone(undefined).label).toBe("Not scored");
  });

  it("no longer defines a colored top strip", () => {
    for (const style of Object.values(FIT_SCORE_STYLES)) {
      expect(style).not.toHaveProperty("strip");
    }
  });
});
