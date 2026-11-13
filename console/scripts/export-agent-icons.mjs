// Export the agent avatar icon set for the backend's /avatar.svg endpoint.
//
// presets.ts is the single source of truth for which lucide icons and colors
// an agent avatar may use. This writes their SVG nodes and the palette to
// src/qwenpaw/app/agent_avatar_icons.json, so the server draws the same
// avatars as the console without shipping lucide itself.
//
// Run after editing presets.ts:  node scripts/export-agent-icons.mjs
// (Node >= 22.18 imports the .ts file directly.)
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  AGENT_ICONS,
  AGENT_ICON_COLORS,
  DEFAULT_ICON_COLOR,
} from "../src/components/AgentAvatar/presets.ts";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const lucideEsm = resolve(
  dirname(require.resolve("lucide-react/package.json")),
  "dist/esm/icons",
);
const out = resolve(here, "../../src/qwenpaw/app/agent_avatar_icons.json");

const icons = {};
// "bot" is always exported: the avatar falls back to it for unknown names.
for (const name of new Set(["bot", ...Object.keys(AGENT_ICONS)])) {
  const mod = await import(pathToFileURL(`${lucideEsm}/${name}.js`).href);
  // Drop React's `key`; keep only the SVG attributes.
  icons[name] = mod.__iconNode.map(([tag, { key: _key, ...attrs }]) => [
    tag,
    attrs,
  ]);
}

const data = {
  defaultColor: DEFAULT_ICON_COLOR,
  colors: AGENT_ICON_COLORS,
  icons,
};
writeFileSync(out, `${JSON.stringify(data, null, 2)}\n`);
console.log(
  `wrote ${Object.keys(icons).length} icons, ${
    Object.keys(AGENT_ICON_COLORS).length
  } colors -> ${out}`,
);
