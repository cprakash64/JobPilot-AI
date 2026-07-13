import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#17211b",
        panel: "#f6f7f2",
        line: "#d8ddd2",
        pine: "#1f5e45",
        coral: "#d45f45",
        sky: "#2f6f9f"
      }
    }
  },
  plugins: []
};

export default config;

