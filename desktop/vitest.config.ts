import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// 独立于 vite.config.ts：测试只需要 node/jsdom 环境，不加载 Electron 相关插件。
export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        globals: true,
        include: ["src/**/*.test.{ts,tsx}"],
        setupFiles: ["./src/test/setup.ts"],
    },
});
