// Aurora Keyboard Shortcuts Manager
import { useEffect, useCallback, useRef } from "react";

export interface Shortcut {
    id: string;
    label: string;
    keys: string;
    mods: {
        ctrl: boolean;
        alt: boolean;
        shift: boolean;
        meta: boolean;
    };
    handler: () => void;
}

const SHORTCUTS_STORAGE_KEY = "aurora_shortcuts";

export const defaultShortcuts: Omit<Shortcut, "handler">[] = [
    { id: "send",          label: "发送消息",           keys: "Enter",    mods: { ctrl: true,  alt: false, shift: false, meta: false } },
    { id: "commandPalette",label: "命令面板",           keys: "k",        mods: { ctrl: true,  alt: false, shift: false, meta: false } },
    { id: "clearChat",     label: "清空聊天",           keys: "l",        mods: { ctrl: true,  alt: false, shift: false, meta: false } },
    { id: "toggleSidebar", label: "切换侧边栏",     keys: "b",        mods: { ctrl: true,  alt: false, shift: false, meta: false } },
    { id: "toggleTerminal",label: "切换终端",           keys: "`",        mods: { ctrl: true,  alt: false, shift: false, meta: false } },
    { id: "settings",      label: "打开设置",           keys: ",",        mods: { ctrl: true,  alt: false, shift: true,  meta: false } },
    { id: "newSession",    label: "新建会话",           keys: "n",        mods: { ctrl: true,  alt: false, shift: false, meta: false } },
    { id: "toggleSearch",  label: "切换搜索",           keys: "f",        mods: { ctrl: true,  alt: false, shift: true,  meta: false } },
];

export class ShortcutManager {
    private handlers: Map<string, () => void> = new Map();
    private userOverrides: Map<string, Omit<Shortcut, "handler">> = new Map();

    constructor() {
        this.loadOverrides();
    }

    private getKeyString(desc: Omit<Shortcut, "handler">): string {
        const parts: string[] = [];
        if (desc.mods.ctrl) parts.push("Ctrl");
        if (desc.mods.alt) parts.push("Alt");
        if (desc.mods.shift) parts.push("Shift");
        if (desc.mods.meta) parts.push("Meta");
        parts.push(desc.keys.length === 1 ? desc.keys.toUpperCase() : desc.keys);
        return parts.join("+");
    }

    private getDescriptiveKeyString(mods: Shortcut["mods"], key: string): string {
        const parts: string[] = [];
        if (mods.ctrl) parts.push("Ctrl");
        if (mods.alt) parts.push("Alt");
        if (mods.shift) parts.push("Shift");
        if (mods.meta) parts.push("Meta");
        parts.push(key.length === 1 ? key.toUpperCase() : key);
        return parts.join("+");
    }

    register(id: string, handler: () => void): void {
        this.handlers.set(id, handler);
    }

    unregister(id: string): void {
        this.handlers.delete(id);
    }

    getBinding(id: string): Omit<Shortcut, "handler"> | undefined {
        if (this.userOverrides.has(id)) {
            return this.userOverrides.get(id);
        }
        return defaultShortcuts.find((s) => s.id === id);
    }

    getKeyLabel(id: string): string {
        const binding = this.getBinding(id);
        if (!binding) return "";
        return this.getDescriptiveKeyString(binding.mods, binding.keys);
    }

    getAllBindings(): Array<{ id: string; label: string; display: string; defaultDisplay: string; isCustom: boolean }> {
        return defaultShortcuts.map((s) => {
            const current = this.userOverrides.get(s.id) || s;
            return {
                id: s.id,
                label: s.label,
                display: this.getDescriptiveKeyString(current.mods, current.keys),
                defaultDisplay: this.getDescriptiveKeyString(s.mods, s.keys),
                isCustom: this.userOverrides.has(s.id),
            };
        });
    }

    setOverride(id: string, mods: Shortcut["mods"], keys: string): void {
        this.userOverrides.set(id, { id, label: "", keys, mods });
        this.persistOverrides();
    }

    resetOverride(id: string): void {
        this.userOverrides.delete(id);
        this.persistOverrides();
    }

    handleKeyDown(e: KeyboardEvent): void {
        const actualKey = e.key.toLowerCase();
        const actualMods = {
            ctrl: e.ctrlKey || e.metaKey,
            alt: e.altKey,
            shift: e.shiftKey,
            meta: e.metaKey,
        };

        // Check user overrides first, then defaults
        for (const [id, handler] of this.handlers) {
            const binding = this.getBinding(id);
            if (!binding) continue;
            const expectedKey = binding.keys.toLowerCase();
            if (
                actualKey === expectedKey &&
                actualMods.ctrl === binding.mods.ctrl &&
                actualMods.alt === binding.mods.alt &&
                actualMods.shift === binding.mods.shift
            ) {
                e.preventDefault();
                handler();
                return;
            }
        }
    }

    private loadOverrides(): void {
        try {
            const raw = localStorage.getItem(SHORTCUTS_STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                for (const [id, binding] of Object.entries(parsed)) {
                    this.userOverrides.set(id, binding as Omit<Shortcut, "handler">);
                }
            }
        } catch {}
    }

    private persistOverrides(): void {
        try {
            const obj: Record<string, any> = {};
            this.userOverrides.forEach((v, k) => {
                obj[k] = { keys: v.keys, mods: v.mods };
            });
            localStorage.setItem(SHORTCUTS_STORAGE_KEY, JSON.stringify(obj));
        } catch {}
    }
}

let globalManager: ShortcutManager | null = null;

export function getShortcutManager(): ShortcutManager {
    if (!globalManager) {
        globalManager = new ShortcutManager();
    }
    return globalManager;
}

export function useGlobalShortcuts(): void {
    const managerRef = useRef(getShortcutManager());

    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            managerRef.current.handleKeyDown(e);
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, []);
}
