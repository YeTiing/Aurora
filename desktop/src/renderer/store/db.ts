// Aurora IndexedDB 持久化层
import type { Session, AgentMessage, PlanStep } from "../../shared/types";

const DB_NAME = "aurora-sessions";
const DB_VERSION = 2;
const STORE_SESSIONS = "sessions";
const STORE_SETTINGS = "settings";

function openDB(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);

        req.onupgradeneeded = (event) => {
            const db = (event.target as IDBOpenDBRequest).result;
            if (!db.objectStoreNames.contains(STORE_SESSIONS)) {
                const store = db.createObjectStore(STORE_SESSIONS, { keyPath: "id" });
                store.createIndex("updatedAt", "updatedAt", { unique: false });
                store.createIndex("workspace", "workspace", { unique: false });
            }
            if (!db.objectStoreNames.contains(STORE_SETTINGS)) {
                db.createObjectStore(STORE_SETTINGS, { keyPath: "key" });
            }
        };

        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

// ── Session CRUD ──
export async function saveSession(session: Session): Promise<void> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readwrite");
        const store = tx.objectStore(STORE_SESSIONS);
        store.put(session);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

export async function loadAllSessions(): Promise<Session[]> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readonly");
        const store = tx.objectStore(STORE_SESSIONS);
        const index = store.index("updatedAt");
        const req = index.openCursor(null, "prev");
        const sessions: Session[] = [];

        req.onsuccess = (event) => {
            const cursor = (event.target as IDBRequest<IDBCursorWithValue>).result;
            if (cursor) {
                sessions.push(cursor.value);
                cursor.continue();
            } else {
                db.close();
                resolve(sessions);
            }
        };
        req.onerror = () => { db.close(); reject(req.error); };
    });
}

export async function loadSession(id: string): Promise<Session | null> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readonly");
        const store = tx.objectStore(STORE_SESSIONS);
        const req = store.get(id);
        req.onsuccess = () => { db.close(); resolve(req.result || null); };
        req.onerror = () => { db.close(); reject(req.error); };
    });
}

export async function deleteSession(id: string): Promise<void> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readwrite");
        const store = tx.objectStore(STORE_SESSIONS);
        store.delete(id);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

export async function deleteAllSessions(): Promise<void> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readwrite");
        const store = tx.objectStore(STORE_SESSIONS);
        store.clear();
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

// ── Settings ──
export async function saveSetting(key: string, value: any): Promise<void> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SETTINGS, "readwrite");
        const store = tx.objectStore(STORE_SETTINGS);
        store.put({ key, value });
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

export async function loadSetting(key: string): Promise<any> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SETTINGS, "readonly");
        const store = tx.objectStore(STORE_SETTINGS);
        const req = store.get(key);
        req.onsuccess = () => { db.close(); resolve(req.result?.value); };
        req.onerror = () => { db.close(); reject(req.error); };
    });
}

// ── 批量保存（应用启动/关闭时）──
export async function saveAllSessions(sessions: Session[]): Promise<void> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readwrite");
        const store = tx.objectStore(STORE_SESSIONS);
        for (const session of sessions) {
            store.put(session);
        }
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

// ── 统计 ──
export async function getStorageStats(): Promise<{ sessionCount: number; totalMessages: number }> {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSIONS, "readonly");
        const store = tx.objectStore(STORE_SESSIONS);
        const req = store.getAll();
        req.onsuccess = () => {
            const sessions = req.result as Session[];
            db.close();
            resolve({
                sessionCount: sessions.length,
                totalMessages: sessions.reduce((sum, s) => sum + s.messages.length, 0),
            });
        };
        req.onerror = () => { db.close(); reject(req.error); };
    });
}