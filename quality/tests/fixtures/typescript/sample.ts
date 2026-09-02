// Conformance fixture: known escapes and one function of known complexity.
export const a: any = 1;
// @ts-ignore
export const b = (a as any).c;
const d = a!.e;
/* eslint-disable */
it.skip("x", () => {});
const notAnEscape = "Company"; // "any" inside a word is not an escape

export function branchy(n: number): number {
  if (n === 1) { return 1; }
  if (n === 2) { return 2; }
  if (n === 3) { return 3; }
  if (n === 4) { return 4; }
  return 0;
}
