import { test } from "node:test";
import assert from "node:assert/strict";
import { selectPlatform, detectPlatform } from "./platform.mjs";

test("Mac client hints choose the correct architecture despite an Intel user-agent", () => {
  const userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)";
  assert.equal(selectPlatform({ userAgent, platform: "macOS", architecture: "arm", bitness: "64" }), "macos-aarch64");
  assert.equal(selectPlatform({ userAgent, architecture: "x86", bitness: "64" }), "macos-x86_64");
  assert.equal(selectPlatform({ userAgent }), "macos-unknown");
});

test("supported Windows and Linux computers receive their own installers", () => {
  assert.equal(selectPlatform({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" }), "windows-x86_64");
  assert.equal(selectPlatform({ userAgent: "Mozilla/5.0 (X11; Linux x86_64)" }), "linux-x86_64");
});

test("ARM hints override reduced x64 user-agents", () => {
  assert.equal(selectPlatform({ userAgent: "Mozilla/5.0 (X11; Linux x86_64)", architecture: "arm", bitness: "64" }), "unsupported");
  assert.equal(selectPlatform({ platform: "Windows", architecture: "arm", bitness: "64" }), "unsupported");
});

test("phones and iPads do not receive desktop installers", () => {
  assert.equal(selectPlatform({ userAgent: "Mozilla/5.0 (Linux; Android 16)", platform: "Linux" }), "mobile");
  assert.equal(selectPlatform({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", platform: "MacIntel", maxTouchPoints: 5 }), "mobile");
  assert.equal(selectPlatform({ platform: "Windows", mobile: true }), "mobile");
});

test("unknown and 32-bit platforms retain a chooser", () => {
  assert.equal(selectPlatform(), "unknown");
  assert.equal(selectPlatform({ platform: "Windows", architecture: "x86", bitness: "32" }), "unknown");
  assert.equal(selectPlatform({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", architecture: "x86", bitness: "32" }), "unknown");
});

test("blocked client hints preserve the Mac chooser", async () => {
  assert.equal(await detectPlatform({
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    userAgentData: { platform: "macOS", async getHighEntropyValues() { throw new Error("blocked"); } },
  }), "macos-unknown");
});
