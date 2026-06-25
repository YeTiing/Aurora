import { useState, useCallback } from "react";

export function usePanels() {
  const [showMemory, setShowMemory] = useState(false);
  const [showSkins, setShowSkins] = useState(false);
  const [showRe, setShowRe] = useState(false);
  const [showDetective, setShowDetective] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showGoal, setShowGoal] = useState(false);
  const [showBrowser, setShowBrowser] = useState(false);
  const [showSocial, setShowSocial] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [activeCenterTab, setActiveCenterTab] = useState<"chat" | "editor" | "plan" | "terminal" | "monitor">("chat");

  const allPanels = {
    memory: { show: showMemory, toggle: useCallback(() => setShowMemory((v) => !v), []) },
    skins: { show: showSkins, toggle: useCallback(() => setShowSkins((v) => !v), []) },
    re: { show: showRe, toggle: useCallback(() => setShowRe((v) => !v), []) },
    detective: { show: showDetective, toggle: useCallback(() => setShowDetective((v) => !v), []) },
    admin: { show: showAdmin, toggle: useCallback(() => setShowAdmin((v) => !v), []) },
    goal: { show: showGoal, toggle: useCallback(() => setShowGoal((v) => !v), []) },
    browser: { show: showBrowser, toggle: useCallback(() => setShowBrowser((v) => !v), []) },
    social: { show: showSocial, toggle: useCallback(() => setShowSocial((v) => !v), []) },
    commandPalette: { show: showCommandPalette, toggle: useCallback(() => setShowCommandPalette((v) => !v), []) },
  } as const;

  return { ...allPanels, activeCenterTab, setActiveCenterTab };
}
