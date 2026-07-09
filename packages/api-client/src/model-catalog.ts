import type { SessionHarnessType } from "./client";

export type HarnessModelProvider = "anthropic" | "openai" | "amp" | "amazon-bedrock" | string;

export interface HarnessModelOption {
  id: string;
  label: string;
  group: string;
  harnessType: SessionHarnessType;
  model?: string;
  provider?: HarnessModelProvider;
  shortcut?: string;
  badge?: string;
  favorite?: boolean;
}

export interface HarnessModelGroup {
  id: string;
  label: string;
  harnessType: SessionHarnessType;
  options: HarnessModelOption[];
}

export const HARNESS_MODEL_GROUPS: HarnessModelGroup[] = [
  {
    id: "claudecode",
    label: "Claude Code",
    harnessType: "claudecode",
    options: [
      {
        id: "claudecode:claude-sonnet-5",
        label: "Sonnet 5 1M",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-sonnet-5",
        provider: "anthropic",
        shortcut: "1",
        badge: "NEW",
      },
      {
        id: "claudecode:claude-fable-5",
        label: "Fable 5",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-fable-5",
        provider: "anthropic",
        shortcut: "2",
      },
      {
        id: "claudecode:claude-opus-4-8",
        label: "Opus 4.8 1M",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-opus-4-8",
        provider: "anthropic",
        shortcut: "3",
      },
      {
        id: "claudecode:claude-opus-4-7",
        label: "Opus 4.7 1M",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-opus-4-7",
        provider: "anthropic",
        shortcut: "4",
      },
      {
        id: "claudecode:claude-opus-4-6",
        label: "Opus 4.6 1M",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-opus-4-6",
        provider: "anthropic",
        shortcut: "5",
      },
      {
        id: "claudecode:claude-sonnet-4-6-1m",
        label: "Sonnet 4.6 1M",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-sonnet-4-6-1m",
        provider: "anthropic",
        shortcut: "6",
      },
      {
        id: "claudecode:claude-sonnet-4-6",
        label: "Sonnet 4.6",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-sonnet-4-6",
        provider: "anthropic",
        shortcut: "7",
      },
      {
        id: "claudecode:claude-haiku-4-5",
        label: "Haiku 4.5",
        group: "Claude Code",
        harnessType: "claudecode",
        model: "claude-haiku-4-5",
        provider: "anthropic",
        shortcut: "8",
      },
    ],
  },
  {
    id: "codex",
    label: "Codex",
    harnessType: "codex",
    options: [
      {
        id: "codex:gpt-5.5",
        label: "GPT-5.5",
        group: "Codex",
        harnessType: "codex",
        model: "gpt-5.5",
        provider: "openai",
        shortcut: "9",
        favorite: true,
      },
      {
        id: "codex:gpt-5.4",
        label: "GPT-5.4",
        group: "Codex",
        harnessType: "codex",
        model: "gpt-5.4",
        provider: "openai",
      },
    ],
  },
  {
    id: "amp",
    label: "Amp",
    harnessType: "amp",
    options: [
      {
        id: "amp:deep",
        label: "Deep",
        group: "Amp",
        harnessType: "amp",
        model: "deep",
        provider: "amp",
      },
      {
        id: "amp:smart",
        label: "Smart",
        group: "Amp",
        harnessType: "amp",
        model: "smart",
        provider: "amp",
      },
      {
        id: "amp:rush",
        label: "Rush",
        group: "Amp",
        harnessType: "amp",
        model: "rush",
        provider: "amp",
      },
    ],
  },
];

export const HARNESS_MODEL_OPTIONS: HarnessModelOption[] = HARNESS_MODEL_GROUPS.flatMap(
  (group) => group.options,
);

export const DEFAULT_HARNESS_MODEL_ID = "codex:gpt-5.5";

export function findHarnessModelOption(id: string): HarnessModelOption | undefined {
  return HARNESS_MODEL_OPTIONS.find((option) => option.id === id);
}

export function harnessModelPayload(id: string): Pick<
  HarnessModelOption,
  "harnessType" | "model" | "provider"
> {
  const option = findHarnessModelOption(id);
  if (!option) {
    throw new Error(`Unknown harness model option: ${id}`);
  }
  return {
    harnessType: option.harnessType,
    model: option.model,
    provider: option.provider,
  };
}
