/**
 * Aurora store 测试 —— 覆盖会话生命周期与 LLM 设置。
 *
 * 说明：本文件是本项目桌面端的首个测试。此前 desktop/ 下测试数为 0，
 * 所有前端逻辑（会话管理、计划步骤、工具日志）只有人工点按验证。
 * 这里刻意只测「纯逻辑/状态迁移」，不做 UI 快照 —— 快照对一个仍在
 * 快速变动的界面维护成本高、信号弱。
 */
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./index";

// 每个用例前重置到干净状态，避免用例间通过模块级 store 相互污染
function resetStore() {
    const s = useStore.getState();
    for (const sess of [...s.sessions]) {
        s.deleteSession(sess.id);
    }
    useStore.setState({
        sessions: [],
        activeSessionId: null,
        sessionsLoaded: false,
    });
}

describe("会话生命周期", () => {
    beforeEach(resetStore);

    it("createSession 返回 id 并置为当前会话", () => {
        const id = useStore.getState().createSession("/tmp/ws");

        expect(id).toBeTruthy();
        const s = useStore.getState();
        expect(s.activeSessionId).toBe(id);
        expect(s.sessions).toHaveLength(1);
        expect(s.sessions[0].workspace).toBe("/tmp/ws");
    });

    it("createSession 不传 workspace 时使用默认值", () => {
        const id = useStore.getState().createSession();
        const sess = useStore.getState().sessions.find((x) => x.id === id);

        expect(sess).toBeDefined();
        expect(typeof sess!.workspace).toBe("string");
    });

    it("多个会话的 id 互不相同", () => {
        const s = useStore.getState();
        const a = s.createSession();
        const b = s.createSession();

        expect(a).not.toBe(b);
        expect(useStore.getState().sessions).toHaveLength(2);
    });

    it("setActiveSession 切换当前会话", () => {
        const s = useStore.getState();
        const a = s.createSession();
        const b = s.createSession();

        useStore.getState().setActiveSession(a);
        expect(useStore.getState().activeSessionId).toBe(a);

        useStore.getState().setActiveSession(b);
        expect(useStore.getState().activeSessionId).toBe(b);
    });

    it("deleteSession 移除会话，并在删除当前会话时清理 activeSessionId", () => {
        const s = useStore.getState();
        const id = s.createSession();
        expect(useStore.getState().activeSessionId).toBe(id);

        useStore.getState().deleteSession(id);

        const after = useStore.getState();
        expect(after.sessions.find((x) => x.id === id)).toBeUndefined();
        // 删除的是当前会话，activeSessionId 不能继续指向已删除的对象
        expect(after.activeSessionId).not.toBe(id);
    });

    it("renameSession 只改动目标会话的标题", () => {
        const s = useStore.getState();
        const a = s.createSession();
        const b = s.createSession();

        useStore.getState().renameSession(a, "重命名后的标题");

        const st = useStore.getState();
        expect(st.sessions.find((x) => x.id === a)!.title).toBe("重命名后的标题");
        expect(st.sessions.find((x) => x.id === b)!.title).not.toBe("重命名后的标题");
    });

    it("duplicateSession 生成新 id 且内容独立", () => {
        const s = useStore.getState();
        const a = s.createSession("/tmp/orig");
        useStore.getState().renameSession(a, "原始会话");

        const copyId = useStore.getState().duplicateSession(a);

        expect(copyId).toBeTruthy();
        expect(copyId).not.toBe(a);
        const st = useStore.getState();
        const copy = st.sessions.find((x) => x.id === copyId);
        expect(copy).toBeDefined();
        expect(copy!.title).toContain("原始会话");

        // 改副本不影响原件
        useStore.getState().renameSession(copyId, "副本改名");
        expect(st.sessions.find((x) => x.id === a)!.title).toBe("原始会话");
    });

    it("togglePinSession 在置顶/取消之间往返，且不影响其他会话", () => {
        const s = useStore.getState();
        const a = s.createSession();
        const b = s.createSession();

        useStore.getState().togglePinSession(a);
        const afterPin = useStore.getState().sessions;
        expect(afterPin.find((x) => x.id === a)!.pinned).toBe(true);
        expect(afterPin.find((x) => x.id === b)!.pinned).toBeFalsy();

        useStore.getState().togglePinSession(a);
        expect(
            useStore.getState().sessions.find((x) => x.id === a)!.pinned,
        ).toBe(false);
    });

    it("toggleArchiveSession 在归档/取消之间往返", () => {
        const s = useStore.getState();
        const a = s.createSession();

        useStore.getState().toggleArchiveSession(a);
        expect(
            useStore.getState().sessions.find((x) => x.id === a)!.archived,
        ).toBe(true);

        useStore.getState().toggleArchiveSession(a);
        expect(
            useStore.getState().sessions.find((x) => x.id === a)!.archived,
        ).toBe(false);
    });
});

describe("消息与计划", () => {
    beforeEach(resetStore);

    it("addMessage 落到指定会话，且自动补 id/timestamp", () => {
        const id = useStore.getState().createSession();

        useStore.getState().addMessage(id, { role: "user", content: "你好" } as never);

        const sess = useStore.getState().sessions.find((x) => x.id === id)!;
        expect(sess.messages).toHaveLength(1);
        expect(sess.messages[0].content).toBe("你好");
        expect(sess.messages[0].id).toBeTruthy();
        expect(sess.messages[0].timestamp).toBeGreaterThan(0);
    });

    it("addMessage 不会写到别的会话", () => {
        const s = useStore.getState();
        const a = s.createSession();
        const b = s.createSession();

        useStore.getState().addMessage(a, { role: "user", content: "只给 A" } as never);

        expect(
            useStore.getState().sessions.find((x) => x.id === b)!.messages ?? [],
        ).toHaveLength(0);
    });

    it("updatePlanStep 只更新目标步骤的状态", () => {
        const id = useStore.getState().createSession();
        useStore.getState().updatePlan(id, [
            { step: 1, description: "第一步", status: "pending" },
            { step: 2, description: "第二步", status: "pending" },
        ] as never);

        useStore.getState().updatePlanStep(id, 1, "completed" as never);

        const plan = useStore.getState().sessions.find((x) => x.id === id)!.plan!;
        expect(plan[0].status).toBe("pending");
        expect(plan[1].status).toBe("completed");
    });

    it("addToolLog 追加到会话并补时间戳", () => {
        const id = useStore.getState().createSession();

        useStore.getState().addToolLog(id, { tool: "shell_command" } as never);

        const logs = useStore.getState().sessions.find((x) => x.id === id)!.toolLogs!;
        expect(logs).toHaveLength(1);
        expect(logs[0].tool).toBe("shell_command");
        expect(logs[0].timestamp).toBeGreaterThan(0);
    });
});

describe("LLM 设置", () => {
    it("setter 写入对应字段且互不干扰", () => {
        const s = useStore.getState();

        s.setLLMModel("deepseek-chat");
        s.setLLMProvider("deepseek");
        s.setLLMBaseUrl("https://api.deepseek.com");
        s.setLLMMaxContext(128000);
        s.setLLMTemperature(0.3);

        const st = useStore.getState();
        expect(st.llmModel).toBe("deepseek-chat");
        expect(st.llmProvider).toBe("deepseek");
        expect(st.llmBaseUrl).toBe("https://api.deepseek.com");
        expect(st.llmMaxContext).toBe(128000);
        expect(st.llmTemperature).toBeCloseTo(0.3);
    });

    it("setLLMApiKey 能写入（用于连通性配置）", () => {
        useStore.getState().setLLMApiKey("sk-test-value");
        expect(useStore.getState().llmApiKey).toBe("sk-test-value");
    });
});

describe("后端连接状态", () => {
    it("setBackendConnected 反映连接与断开", () => {
        useStore.getState().setBackendConnected(true);
        expect(useStore.getState().backendConnected).toBe(true);

        useStore.getState().setBackendConnected(false);
        expect(useStore.getState().backendConnected).toBe(false);
    });
});
