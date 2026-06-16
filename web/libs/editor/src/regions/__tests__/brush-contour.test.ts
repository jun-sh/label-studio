import { generateOutlineFromCanvas } from "../BitmaskRegion/contour";

describe("generateOutlineFromCanvas", () => {
  it("returns scaled outline for a filled square mask", () => {
    const canvas = document.createElement("canvas");
    canvas.width = 10;
    canvas.height = 10;
    const ctx = canvas.getContext("2d");

    expect(ctx).not.toBeNull();
    ctx!.fillStyle = "rgba(255, 0, 0, 1)";
    ctx!.fillRect(2, 2, 5, 5);

    const outlines = generateOutlineFromCanvas(canvas, 2, 2);

    expect(outlines.length).toBeGreaterThan(0);
    expect(outlines[0].length).toBeGreaterThan(8);
  });

  it("returns empty array for transparent canvas", () => {
    const canvas = document.createElement("canvas");
    canvas.width = 10;
    canvas.height = 10;

    expect(generateOutlineFromCanvas(canvas, 1, 1)).toEqual([]);
  });
});
