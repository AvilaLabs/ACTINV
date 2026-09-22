// Client hints distinguish Apple Silicon from Intel when the browser exposes it.
// A Mac user-agent alone is insufficient: Apple Silicon often reports Intel.
export function selectPlatform({ userAgent = "", platform = "", architecture = "", bitness = "", mobile = false, maxTouchPoints = 0 } = {}) {
  if (mobile || /Android|iPhone|iPad|iPod/i.test(userAgent) || (/Mac/i.test(platform || userAgent) && maxTouchPoints > 1)) return "mobile";
  const arm = /arm|aarch64/i.test(architecture);
  const x64 = /x86|amd64/i.test(architecture) && bitness === "64";
  if (/mac/i.test(platform) || /Macintosh/i.test(userAgent)) {
    if (arm && bitness === "64") return "macos-aarch64";
    if (x64) return "macos-x86_64";
    return "macos-unknown";
  }
  if (/windows/i.test(platform) || /Windows NT/i.test(userAgent)) {
    if (arm || /Windows.*ARM/i.test(userAgent)) return "unsupported";
    if (x64 || (!architecture && /Win64|WOW64|x86_64/i.test(userAgent))) return "windows-x86_64";
    return "unknown";
  }
  if (/linux/i.test(platform) || /Linux/i.test(userAgent)) {
    if (arm || /aarch64|armv\d/i.test(userAgent)) return "unsupported";
    if (x64 || (!architecture && /x86_64|amd64/i.test(userAgent))) return "linux-x86_64";
  }
  return "unknown";
}

export async function detectPlatform(browser = navigator) {
  const info = {
    userAgent: browser.userAgent,
    platform: browser.userAgentData?.platform || browser.platform,
    mobile: browser.userAgentData?.mobile,
    maxTouchPoints: browser.maxTouchPoints,
  };
  let timeout;
  try {
    if (browser.userAgentData?.getHighEntropyValues) {
      const hints = await Promise.race([
        browser.userAgentData.getHighEntropyValues(["architecture", "bitness"]),
        new Promise(resolve => { timeout = setTimeout(() => resolve({}), 1500); }),
      ]);
      Object.assign(info, hints);
    }
  } catch {
    // Privacy settings may withhold hints; retain the explicit platform chooser.
  } finally {
    clearTimeout(timeout);
  }
  return selectPlatform(info);
}
