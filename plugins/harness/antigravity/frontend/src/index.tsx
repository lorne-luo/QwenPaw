import { getHost } from "@qwenpaw/plugin-sdk";
import { Settings } from "./Settings";
getHost().harnesses.register("qwenpaw-harness-antigravity", {
  backendId: "antigravity",
  SettingsComponent: Settings,
});
