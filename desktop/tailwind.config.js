/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    darkMode: "class",
    theme: {
        extend: {
            colors: {
                aurora: {
                    bg: "#0d1117",
                    "bg-secondary": "#161b22",
                    surface: "#161b22",
                    border: "#30363d",
                    text: "#e6edf3",
                    "text-secondary": "#8b949e",
                    accent: "#58a6ff",
                    "accent-hover": "#79c0ff",
                    error: "#f85149",
                    success: "#3fb950",
                    warning: "#d29922",
                },
                "aurora-light": {
                    bg: "#ffffff",
                    "bg-secondary": "#f6f8fa",
                    surface: "#ffffff",
                    border: "#d0d7de",
                    text: "#1f2328",
                    "text-secondary": "#656d76",
                    accent: "#0969da",
                    "accent-hover": "#0550ae",
                    error: "#cf222e",
                    success: "#1a7f37",
                    warning: "#9a6700",
                },
            },
            fontFamily: {
                mono: ["'Cascadia Code'", "'Fira Code'", "Consolas", "monospace"],
                sans: ["system-ui", "sans-serif"],
            },
            animation: {
                "fade-in": "fadeIn 0.2s ease-in-out",
                "slide-up": "slideUp 0.3s ease-out",
                "pulse-dot": "pulseDot 1.5s ease-in-out infinite",
            },
            keyframes: {
                fadeIn: {
                    "0%": { opacity: "0" },
                    "100%": { opacity: "1" },
                },
                slideUp: {
                    "0%": { opacity: "0", transform: "translateY(8px)" },
                    "100%": { opacity: "1", transform: "translateY(0)" },
                },
                pulseDot: {
                    "0%, 100%": { opacity: "1" },
                    "50%": { opacity: "0.3" },
                },
            },
        },
    },
    plugins: [],
};