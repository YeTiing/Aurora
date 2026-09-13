// 测试环境准备：store 依赖 IndexedDB，jsdom 不提供，用 fake-indexeddb 替代，
// 使测试能真实走一遍持久化路径而不是打桩掉。
import "fake-indexeddb/auto";
