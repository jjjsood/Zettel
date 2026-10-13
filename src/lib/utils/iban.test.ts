import { describe, expect, it } from "vitest";
import { formatIban, isValidIban } from "./iban";

describe("formatIban", () => {
  it("groups by 4, uppercases, strips junk", () => {
    expect(formatIban("de89-3704 0044053201 3000")).toBe("DE89 3704 0044 0532 0130 00");
  });
  it("no trailing space on full groups", () => {
    expect(formatIban("DE89")).toBe("DE89");
  });
});

describe("isValidIban", () => {
  it("accepts valid DE / GB / AT", () => {
    expect(isValidIban("DE89 3704 0044 0532 0130 00")).toBe(true);
    expect(isValidIban("gb82west12345698765432")).toBe(true);
    expect(isValidIban("AT611904300234573201")).toBe(true);
  });
  it("rejects bad checksum, wrong DE length, junk", () => {
    expect(isValidIban("DE89370400440532013001")).toBe(false);
    expect(isValidIban("DE8937040044053201300")).toBe(false);
    expect(isValidIban("hello")).toBe(false);
    expect(isValidIban("")).toBe(false);
  });
});
