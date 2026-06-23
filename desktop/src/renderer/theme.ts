// Aurora Theme Engine
import { useState, useEffect, useCallback } from "react";
import { useStore } from "./store";

export interface ThemeColors {
    bg: string;
    surface: string;
    border: string;
    text: string;
    textSecondary: string;
    accent: string;
    error: string;
    success: string;
    warning: string;
    code: string;
}

export interface Theme {
    name: string;
    label: string;
    colors: ThemeColors;
}

import { animeThemes } from "./anime_themes";

export const themes: Theme[] = [
    ...animeThemes,
    {
        name: "aurora-dark",
        label: "Aurora Dark",
        colors: {
            bg: "#0d1117",
            surface: "#161b22",
            border: "#30363d",
            text: "#e6edf3",
            textSecondary: "#8b949e",
            accent: "#58a6ff",
            error: "#f85149",
            success: "#3fb950",
            warning: "#d29922",
            code: "#1c2333",
        },
    },
    {
        name: "aurora-light",
        label: "Aurora Light",
        colors: {
            bg: "#ffffff",
            surface: "#f6f8fa",
            border: "#d0d7de",
            text: "#1f2328",
            textSecondary: "#656d76",
            accent: "#0969da",
            error: "#cf222e",
            success: "#1a7f37",
            warning: "#9a6700",
            code: "#f6f8fa",
        },
    },
    {
        name: "monokai",
        label: "Monokai",
        colors: {
            bg: "#272822",
            surface: "#3e3d32",
            border: "#49483e",
            text: "#f8f8f2",
            textSecondary: "#75715e",
            accent: "#a6e22e",
            error: "#f92672",
            success: "#a6e22e",
            warning: "#e6db74",
            code: "#2e2f2a",
        },
    },
    {
        name: "solarized-dark",
        label: "Solarized Dark",
        colors: {
            bg: "#002b36",
            surface: "#073642",
            border: "#586e75",
            text: "#839496",
            textSecondary: "#586e75",
            accent: "#268bd2",
            error: "#dc322f",
            success: "#859900",
            warning: "#b58900",
            code: "#073642",
        },
    },
    {
        name: "github-light",
        label: "GitHub Light",
        colors: {
            bg: "#ffffff",
            surface: "#f6f8fa",
            border: "#d0d7de",
            text: "#24292f",
            textSecondary: "#57606a",
            accent: "#0969da",
            error: "#cf222e",
            success: "#1a7f37",
            warning: "#9a6700",
            code: "#f6f8fa",
        },
    },
];

const THEME_STORAGE_KEY = "aurora_active_theme";

function cssVarName(key: string): string {
    return `--aurora-${key.replace(/([A-Z])/g, "-$1").toLowerCase()}`;
}

export function applyTheme(theme: Theme): void {
    const root = document.documentElement;
    const colorKeys = Object.keys(theme.colors) as Array<keyof ThemeColors>;
    for (const key of colorKeys) {
        root.style.setProperty(cssVarName(key), theme.colors[key]);
    }
    root.setAttribute("data-theme", theme.name);
    localStorage.setItem(THEME_STORAGE_KEY, theme.name);
}

export function getStoredThemeName(): string {
    return localStorage.getItem(THEME_STORAGE_KEY) || "aurora-dark";
}

export function getThemeByName(name: string): Theme {
    return themes.find((t) => t.name === name) || themes[0];
}

export function useTheme(): [Theme, (name: string) => void] {
    const storeColors = useStore((s: any) => s.themeColors);
    const [activeName, setActiveName] = useState<string>(() => getStoredThemeName());

    const setTheme = useCallback((name: string) => {
        const theme = getThemeByName(name);
        setActiveName(name);
        applyTheme(theme);
        // Also set legacy theme + colors in store for backward compat
        useStore.setState({ themeColors: { ...theme.colors, bgSecondary: theme.colors.surface, accentHover: theme.colors.accent } as any });
    }, []);

    const currentTheme: Theme = {
        name: activeName,
        label: getThemeByName(activeName).label,
        colors: storeColors as ThemeColors,
    };

    return [currentTheme, setTheme];
}

export function useInitializeTheme(): void {
    useEffect(() => {
        const stored = getStoredThemeName();
        const theme = getThemeByName(stored);
        applyTheme(theme);
        useStore.setState({ themeColors: { ...theme.colors, bgSecondary: theme.colors.surface, accentHover: theme.colors.accent } as any });
    }, []);
}
