import {
  Anchor,
  BookOpen,
  Bot,
  Brain,
  Briefcase,
  Bug,
  Camera,
  ChartLine,
  Clapperboard,
  Cloud,
  CodeXml,
  Coffee,
  Compass,
  ConciergeBell,
  Cpu,
  Crown,
  Database,
  Feather,
  Flame,
  FlaskConical,
  Gamepad2,
  Gem,
  Globe,
  GraduationCap,
  Hammer,
  HandPlatter,
  Heart,
  Key,
  Lightbulb,
  Map,
  MessageCircleQuestionMark,
  Mic,
  Music,
  NotebookPen,
  Orbit,
  Palette,
  PenTool,
  Puzzle,
  Radar,
  Rocket,
  Search,
  Server,
  ServerCog,
  ShieldCheck,
  Ship,
  ShipWheel,
  Sparkles,
  Target,
  Telescope,
  Terminal,
  Trophy,
  UserRound,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";

/**
 * Icons offered for agent avatars, keyed by their lucide name. Imported
 * statically so the bundle carries only these, not all ~3800 lucide icons.
 */
export const AGENT_ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  "hand-platter": HandPlatter,
  "concierge-bell": ConciergeBell,
  "user-round": UserRound,
  sparkles: Sparkles,
  brain: Brain,
  cpu: Cpu,
  terminal: Terminal,
  "code-xml": CodeXml,
  server: Server,
  "server-cog": ServerCog,
  database: Database,
  cloud: Cloud,
  "shield-check": ShieldCheck,
  "message-circle-question-mark": MessageCircleQuestionMark,
  bug: Bug,
  wrench: Wrench,
  hammer: Hammer,
  "flask-conical": FlaskConical,
  telescope: Telescope,
  search: Search,
  radar: Radar,
  compass: Compass,
  map: Map,
  globe: Globe,
  rocket: Rocket,
  orbit: Orbit,
  ship: Ship,
  "ship-wheel": ShipWheel,
  anchor: Anchor,
  lightbulb: Lightbulb,
  palette: Palette,
  "pen-tool": PenTool,
  "notebook-pen": NotebookPen,
  feather: Feather,
  camera: Camera,
  clapperboard: Clapperboard,
  mic: Mic,
  music: Music,
  "book-open": BookOpen,
  "graduation-cap": GraduationCap,
  briefcase: Briefcase,
  "chart-line": ChartLine,
  target: Target,
  trophy: Trophy,
  gem: Gem,
  crown: Crown,
  zap: Zap,
  flame: Flame,
  heart: Heart,
  coffee: Coffee,
  "gamepad-2": Gamepad2,
  puzzle: Puzzle,
  key: Key,
};

/** Badge fill per color key: [top-left, bottom-right] gradient stops. */
export const AGENT_ICON_COLORS: Record<string, [string, string]> = {
  blue: ["#60a5fa", "#2563eb"],
  sky: ["#38bdf8", "#0284c7"],
  cyan: ["#22d3ee", "#0891b2"],
  teal: ["#2dd4bf", "#0d9488"],
  green: ["#4ade80", "#16a34a"],
  amber: ["#fbbf24", "#d97706"],
  orange: ["#fb923c", "#ea580c"],
  red: ["#f87171", "#dc2626"],
  pink: ["#f472b6", "#db2777"],
  violet: ["#a78bfa", "#7c3aed"],
  indigo: ["#818cf8", "#4f46e5"],
  slate: ["#94a3b8", "#475569"],
};

export const DEFAULT_ICON_COLOR = "blue";

const PREFIX = "lucide:";

export interface LucideIconValue {
  name: string;
  color: string;
}

/** `lucide:<name>:<color>` -> parts; anything else (emoji, empty) -> null. */
export function parseLucideIcon(value?: string): LucideIconValue | null {
  if (!value?.startsWith(PREFIX)) return null;
  const [name = "", color = ""] = value.slice(PREFIX.length).split(":");
  return { name, color };
}

export function formatLucideIcon(name: string, color: string): string {
  return `${PREFIX}${name}:${color}`;
}
