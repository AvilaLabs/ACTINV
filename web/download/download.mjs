import { detectPlatform } from "./platform.mjs";

const status = document.querySelector("#status");
const primary = document.querySelector("#download");
try {
  const response = await fetch("./releases.json");
  if (!response.ok) throw new Error("Release information is unavailable.");
  const release = await response.json();
  const choices = document.querySelector("#choices");
  for (const asset of Object.values(release.platforms)) {
    const link = document.createElement("a");
    link.href = asset.url;
    link.textContent = asset.label;
    const detail = document.createElement("small");
    detail.textContent = asset.detail;
    link.append(detail);
    choices.append(link);
  }
  const platform = await detectPlatform();
  const selected = release.platforms[platform];
  if (selected) {
    status.textContent = `Starting your ${selected.label} download. If it does not start, use the button below.`;
    primary.textContent = `Download for ${selected.label}`;
    primary.href = selected.url;
    primary.hidden = false;
    window.location.replace(selected.url);
  } else if (platform === "macos-unknown") {
    status.textContent = "Choose the processor in your Mac.";
    document.querySelector("#mac-help").hidden = false;
  } else if (platform === "mobile") {
    status.textContent = "The desktop app needs a Windows, Mac, or Linux computer. You can open the browser workbench on this device.";
  } else if (platform === "unsupported") {
    status.textContent = "There is no native desktop download for this processor yet. You can use the browser workbench or choose a download for another computer.";
  } else {
    status.textContent = "Choose a download for your computer.";
  }
} catch {
  status.textContent = "Choose your download on the desktop release page.";
  primary.href = "https://github.com/AvilaLabs/ACTINV/releases?q=desktop-v&expanded=true";
  primary.textContent = "View desktop downloads";
  primary.hidden = false;
}
