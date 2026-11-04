/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "#052232",
        panel: "#073446",
        panel2: "#0a3d52",
        line: "rgba(25,195,218,0.25)",
        ink: "#E6EDF0",
        muted: "#8B9BA5",
        accent: "#19C3DA",
        up: "#95EB45",
        down: "#F2643C",
        developing: "#7F9BD8",
        forming: "#E3A33B",
        validated: "#36C2A0",
        invalid: "#6F7E87",
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
