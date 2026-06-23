// Aurora 中文语言包 + 主题系统
export type Lang = "zh-CN" | "en";

interface LocaleStrings {
    appName: string;
    files: string;
    chats: string;
    search: string;
    settings: string;
    terminal: string;
    editor: string;
    plan: string;
    commands: string;
    send: string;
    cancel: string;
    newChat: string;
    openFolder: string;
    noFiles: string;
    loading: string;
    thinking: string;
    done: string;
    error: string;
    connected: string;
    disconnected: string;
    session: string;
    sessions: string;
    workspace: string;
    backendStatus: string;
    toggleTheme: string;
    toggleFileTree: string;
    toggleSearch: string;
    shortcutHint: string;
    accept: string;
    reject: string;
    diffAdded: string;
    diffRemoved: string;
    stepCompleted: string;
    stepFailed: string;
    stepPending: string;
    typeCommand: string;
    typeMessage: string;
    noMessages: string;
    askAurora: string;
    searchFiles: string;
    modelSelect: string;
        attachFiles: string;
        remove: string;
        saving: string;
        saveSettings: string;
        settingsSaved: string;
        saveFailed: string;
        resetToDefault: string;
        unknownError: string;
        closePanel: string;
        monitor: string;
        clearChatConfirm: string;
        alt: string;
        ctrl: string;
        shift: string;
        meta: string;
        quickCreateProject: string;
        quickAnalyzeCodebase: string;
        quickExplainCode: string;
        quickWriteTests: string;
        dropFilesToAttach: string;
        welcomeTitle: string;
        welcomeSubtitle: string;
        continueTyping: string;
}

const zhCN: LocaleStrings = {
    appName: "✦ Aurora",
    files: "文件",
    chats: "对话",
    search: "搜索",
    settings: "设置",
    terminal: "终端",
    editor: "编辑器",
    plan: "计划",
    commands: "命令面板",
    send: "发送",
    cancel: "取消",
    newChat: "新对话",
    openFolder: "打开文件夹",
    noFiles: "暂无文件",
    loading: "加载中...",
    thinking: "思考中...",
    done: "完成",
    error: "错误",
    connected: "已连接",
    disconnected: "未连接",
    session: "会话",
    sessions: "会话历史",
    workspace: "工作区",
    backendStatus: "后端状态",
    toggleTheme: "切换主题",
    toggleFileTree: "切换文件树",
    toggleSearch: "切换搜索",
    shortcutHint: "Ctrl+P 命令面板",
    accept: "接受",
    reject: "拒绝",
    diffAdded: "新增",
    diffRemoved: "删除",
    stepCompleted: "已完成",
    stepFailed: "失败",
    stepPending: "待处理",
    typeCommand: "输入命令...",
    typeMessage: "输入消息...",
    noMessages: "开始新对话",
    askAurora: "问 Aurora 任何事...",
    searchFiles: "搜索文件...",
    modelSelect: "选择模型",
        attachFiles: "附加文件",
        remove: "移除",
        saving: "保存中...",
        saveSettings: "保存设置",
        settingsSaved: "设置已保存",
        saveFailed: "保存失败",
        resetToDefault: "恢复默认",
        unknownError: "未知错误",
        closePanel: "关闭面板",
        monitor: "监控",
        clearChatConfirm: "确定清空对话吗？",
        alt: "Alt",
        ctrl: "Ctrl",
        shift: "Shift",
        meta: "Meta",
    quickCreateProject: "创建新项目",
    quickAnalyzeCodebase: "分析代码库",
    quickExplainCode: "解释代码逻辑",
    quickWriteTests: "写单元测试",
                dropFilesToAttach: "拖入文件",
        welcomeTitle: "✦ Aurora",
        welcomeSubtitle: "你的 AI 智能编程助手",
        continueTyping: "输入消息 (Enter换行 Ctrl+Enter发送)...",
};

const en: LocaleStrings = {
    appName: "✦ Aurora",
    files: "Files",
    chats: "Chats",
    search: "Search",
    settings: "Settings",
    terminal: "Terminal",
    editor: "Editor",
    plan: "Plan",
    commands: "Command Palette",
    send: "Send",
    cancel: "Cancel",
    newChat: "New Chat",
    openFolder: "Open Folder",
    noFiles: "No files",
    loading: "Loading...",
    thinking: "Thinking...",
    done: "Done",
    error: "Error",
    connected: "Connected",
    disconnected: "Disconnected",
    session: "Session",
    sessions: "Sessions",
    workspace: "Workspace",
    backendStatus: "Backend",
    toggleTheme: "Toggle Theme",
    toggleFileTree: "Toggle File Tree",
    toggleSearch: "Toggle Search",
    shortcutHint: "Ctrl+P Command Palette",
    accept: "Accept",
    reject: "Reject",
    diffAdded: "Added",
    diffRemoved: "Removed",
    stepCompleted: "Completed",
    stepFailed: "Failed",
    stepPending: "Pending",
    typeCommand: "Type command...",
    typeMessage: "Type message...",
    noMessages: "Start a new chat",
    askAurora: "Ask Aurora anything...",
    searchFiles: "Search files...",
    modelSelect: "Select Model",
        attachFiles: "Attach files",
        remove: "Remove",
        saving: "Saving...",
        saveSettings: "Save Settings",
        settingsSaved: "Settings saved",
        saveFailed: "Save failed",
        resetToDefault: "Reset to default",
        unknownError: "Unknown error",
        closePanel: "Close panel",
        monitor: "Monitor",
        clearChatConfirm: "Clear this conversation?",
        alt: "Alt",
        ctrl: "Ctrl",
        shift: "Shift",
        meta: "Meta",
    quickCreateProject: "Create a new project",
    quickAnalyzeCodebase: "Analyze this codebase",
    quickExplainCode: "Explain how this works",
    quickWriteTests: "Write unit tests",
                dropFilesToAttach: "Drop files to attach",
        welcomeTitle: "✦ Aurora",
        welcomeSubtitle: "Your AI coding companion",
        continueTyping: "Type a message (Enter newline, Ctrl+Enter send)...",
};

const locales: Record<Lang, LocaleStrings> = { "zh-CN": zhCN, en };

let currentLang: Lang = "zh-CN";

export function setLang(lang: Lang) {
    currentLang = lang;
    if (typeof document !== "undefined") {
        document.documentElement.lang = lang;
    }
}

export function getLang(): Lang {
    return currentLang;
}

export function t(key: keyof LocaleStrings): string {
    return locales[currentLang][key] || locales.en[key] || key;
}