import { useState, useEffect, useMemo } from "react";
import { useStore } from "../store";

export function useLayout() {
  const colors = useStore((s) => s.themeColors);
  const [bgImage, setBgImage] = useState(() => localStorage.getItem("aurora_bg_image") || "");
  const [panelOpacityL, setPanelOpacityL] = useState(() => Number(localStorage.getItem("aurora_panel_opacity_left") ?? 0.85));
  const [panelBgL, setPanelBgL] = useState(() => localStorage.getItem("aurora_panel_bg_left") || "");
  const [panelOpacityC, setPanelOpacityC] = useState(() => Number(localStorage.getItem("aurora_panel_opacity_center") ?? 0.85));
  const [panelBgC, setPanelBgC] = useState(() => localStorage.getItem("aurora_panel_bg_center") || "");
  const [panelOpacityR, setPanelOpacityR] = useState(() => Number(localStorage.getItem("aurora_panel_opacity_right") ?? 0.85));
  const [panelBgR, setPanelBgR] = useState(() => localStorage.getItem("aurora_panel_bg_right") || "");

  useEffect(() => { localStorage.setItem("aurora_bg_image", bgImage); }, [bgImage]);
  useEffect(() => { localStorage.setItem("aurora_panel_opacity_left", String(panelOpacityL)); }, [panelOpacityL]);
  useEffect(() => { localStorage.setItem("aurora_panel_bg_left", panelBgL); }, [panelBgL]);
  useEffect(() => { localStorage.setItem("aurora_panel_opacity_center", String(panelOpacityC)); }, [panelOpacityC]);
  useEffect(() => { localStorage.setItem("aurora_panel_bg_center", panelBgC); }, [panelBgC]);
  useEffect(() => { localStorage.setItem("aurora_panel_opacity_right", String(panelOpacityR)); }, [panelOpacityR]);
  useEffect(() => { localStorage.setItem("aurora_panel_bg_right", panelBgR); }, [panelBgR]);

  const rootStyle = useMemo(() => ({
    "--aurora-bg": colors.bg || "#0d1117",
    "--aurora-fg": colors.fg || "#e6edf3",
    "--aurora-accent": colors.accent || "#8b5cf6",
    "--aurora-border": colors.border || "#30363d",
    "--aurora-hover": colors.hover || "#1c2128",
  } as React.CSSProperties), [colors]);

  return {
    rootStyle, colors,
    bgImage, setBgImage,
    panelOpacityL, setPanelOpacityL, panelBgL, setPanelBgL,
    panelOpacityC, setPanelOpacityC, panelBgC, setPanelBgC,
    panelOpacityR, setPanelOpacityR, panelBgR, setPanelBgR,
  };
}
